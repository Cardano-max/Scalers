"""Computed confidence on the slice decision path (AUTON-02 / 4jx.3) — DB-free.

The two hardcoded confidences are gone: ``phase1_slice.ASSEMBLE_CONFIDENCE`` (0.9)
and ``harness.nodes._confidence_for`` (0.6 + 0.1·findings). This file proves the
replacement end-to-end on the graph path:

* the assemble cell's confidence = self-consistency over a K-sample probe — a
  divergent generation reads LOW, a stable one reads HIGH (never a constant);
* an uncomputable probe (too few samples) routes to review — explicitly, even at
  ``threshold=0.0`` (the None→0.0 coercion hole is closed);
* the demo ``AssembleNode`` (pure code) measures an honest 1.0.

All hermetic via ``FunctionModel``; the PG slice tests exercise the same path
against real Postgres with a unanimous probe (confidence 1.0 ≥ 0.85 → auto).
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from harness.nodes import AssembleNode
from harness.state import AutonomyMode, GraphState, ResearchOutput
from phase1_slice import PROBE_K, AssembleCellNode, build_slice_graph
from sideeffects import Channel
from tests.conftest import VALID_BRIEF, tool_model

# Three briefs, identical except the caption (the self-consistency signature):
# validator-safe minimal edits of the shared VALID_BRIEF caption.
_BRIEF_A = dict(VALID_BRIEF)
_BRIEF_B = {**VALID_BRIEF, "caption": VALID_BRIEF["caption"].replace("spring", "summer")}
_BRIEF_C = {**VALID_BRIEF, "caption": VALID_BRIEF["caption"].replace("Three sessions", "Two sessions")}


def _payloads_then_error_model(payloads: list[dict], exc: BaseException) -> FunctionModel:
    """Return each payload in order; once they run out, every call raises ``exc`` —
    models decision/probe samples that succeed until the connector goes down."""
    calls = {"n": 0}

    def fn(messages, info: AgentInfo) -> ModelResponse:
        idx = calls["n"]
        calls["n"] += 1
        if idx >= len(payloads):
            raise exc
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payloads[idx])])

    return FunctionModel(fn)


def _valid_then_error_model(payload: dict, exc: BaseException) -> FunctionModel:
    """First call returns ``payload``; every later call raises ``exc`` — models a
    decision sample that succeeds while every probe sample fails."""
    return _payloads_then_error_model([payload], exc)


async def _run_assemble(model, **node_kw) -> GraphState:
    node = AssembleCellNode(model, **node_kw)
    state = GraphState(tenant_id="t", run_id="r", topic="spring booking push")
    return await node(state)


# ── the probe: computed, never constant ──────────────────────────────────────


def test_unanimous_probe_yields_full_confidence():
    out = asyncio.run(_run_assemble(tool_model(_BRIEF_A)))  # repeats -> 3 identical
    assert out["confidence"] == 1.0


def test_divergent_probe_yields_low_confidence():
    # Both probes disagree with the shipped decision sample -> anchored 0/2.
    out = asyncio.run(_run_assemble(tool_model(_BRIEF_A, _BRIEF_B, _BRIEF_C)))
    assert out["confidence"] == 0.0


def test_partial_agreement_is_between():
    # One of two probes matches the shipped sample -> 1/2.
    out = asyncio.run(_run_assemble(tool_model(_BRIEF_A, _BRIEF_A, _BRIEF_C)))
    assert out["confidence"] == pytest.approx(1 / 2)


def test_probes_agreeing_with_each_other_but_not_shipped_read_zero():
    """REGRESSION (adversarial): modal scoring rated [ship=A, probes=B,B] at 2/3 —
    high confidence describing a DIFFERENT output than the one that ships. Anchored
    scoring reads it 0.0: the probes contradict the shipped draft."""
    out = asyncio.run(_run_assemble(tool_model(_BRIEF_A, _BRIEF_B, _BRIEF_B)))
    assert out["confidence"] == 0.0


def test_high_variance_lower_than_low_variance():
    high_var = asyncio.run(_run_assemble(tool_model(_BRIEF_A, _BRIEF_B, _BRIEF_C)))
    low_var = asyncio.run(_run_assemble(tool_model(_BRIEF_A)))
    assert high_var["confidence"] < low_var["confidence"]  # the AC's core property


def test_probe_failures_yield_uncomputable_none():
    # Decision sample ok; both probe samples raise -> 0 probes < MIN_PROBES.
    model = _valid_then_error_model(_BRIEF_A, ConnectionError("probe down"))
    out = asyncio.run(_run_assemble(model))
    assert out["confidence"] is None  # uncomputable, NOT a fabricated number
    assert out["assembled"] is not None  # the draft itself still assembled


def test_single_probe_failure_at_k3_is_uncomputable():
    # The zero-slack boundary: decision + probe1 succeed, probe2 fails -> 1 probe
    # < MIN_PROBES -> None -> review (fail-safe direction; K=4+ buys slack).
    model = _payloads_then_error_model([_BRIEF_A, _BRIEF_A], ConnectionError("flaky"))
    out = asyncio.run(_run_assemble(model))
    assert out["confidence"] is None


def test_probe_k_below_min_probes_refused():
    with pytest.raises(ValueError, match="MIN_PROBES"):
        AssembleCellNode(None, probe_k=2)
    assert PROBE_K >= 3


# ── graph routing: computed confidence drives the edge ───────────────────────


class _RecordingEnqueue:
    name = "enqueue"

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, state) -> dict:
        self.calls += 1
        return {"step_log": ["enqueue"]}


def _run_graph(model, *, threshold: float) -> tuple[GraphState, _RecordingEnqueue]:
    enq = _RecordingEnqueue()
    graph = build_slice_graph(
        dsn="unused://", tenant_id="t", assemble_model=model,
        autonomy=AutonomyMode.AUTO, threshold=threshold,
        channel=Channel.POSTING, target="feed", enqueue_node=enq,
    )
    state = asyncio.run(graph.run("r1", GraphState(tenant_id="t", run_id="r1", topic="x")))
    return state, enq


def test_stable_generation_auto_enqueues():
    state, enq = _run_graph(tool_model(_BRIEF_A), threshold=0.85)
    assert state.confidence == 1.0 and enq.calls == 1


def test_divergent_generation_reviews_no_enqueue():
    state, enq = _run_graph(tool_model(_BRIEF_A, _BRIEF_B, _BRIEF_C), threshold=0.85)
    assert state.confidence == 0.0  # anchored: no probe matched the shipped draft
    assert enq.calls == 0  # low computed confidence -> review, not auto


def test_uncomputable_reviews_even_at_zero_threshold():
    """REGRESSION (4jx.3 fail-safe): None coerced to 0.0 would satisfy a 0.0
    threshold and auto-fire on 'couldn't compute'. The edge must treat None as
    review BEFORE any numeric comparison."""
    model = _valid_then_error_model(_BRIEF_A, ConnectionError("probe down"))
    state, enq = _run_graph(model, threshold=0.0)
    assert state.confidence is None
    assert enq.calls == 0  # fail-safe held even at the pathological threshold


# ── the demo AssembleNode: honest determinism ────────────────────────────────


def test_demo_assemble_node_measures_honest_full_consistency():
    node = AssembleNode()
    state = GraphState(
        tenant_id="t", run_id="r", topic="x",
        research=ResearchOutput(topic="x", findings=["a", "b"]),
    )
    out = asyncio.run(node(state))
    # Pure-code assembly is deterministic -> measured (not asserted) 1.0; the old
    # 0.6 + 0.1·len(findings) heuristic (would be 0.8 here) is gone.
    assert out["confidence"] == 1.0


def test_demo_assemble_confidence_is_measured_not_constant():
    """REGRESSION (adversarial tautology check): asserting 1.0 alone survives a
    revert to a hardcoded constant. Prove the value is MEASURED from the builds:
    a non-deterministic builder must read < 1.0."""

    class FlakyAssemble(AssembleNode):
        def __init__(self) -> None:
            self._n = 0

        def _build_draft(self, research) -> str:  # type: ignore[override]
            self._n += 1
            return f"draft variant {self._n}"  # different every build

    out = asyncio.run(
        FlakyAssemble()(
            GraphState(
                tenant_id="t", run_id="r", topic="x",
                research=ResearchOutput(topic="x", findings=["a"]),
            )
        )
    )
    assert out["confidence"] < 1.0  # instability is DETECTED, not papered over


# ── run_slice's own None branch (distinct from the graph edge) ───────────────


def test_run_slice_uncomputable_reviews_at_zero_threshold(monkeypatch):
    """REGRESSION (adversarial): run_slice re-derives the decision AFTER the graph;
    a `state.confidence or 0.0` revert there would report AUTO + a fabricated
    ENQUEUED status under a zero-threshold pack even though the graph edge refused
    to enqueue. Pin the None branch with a lifted, auto/threshold-0.0 pack."""
    from config.schema import (
        AutonomyConfig,
        AutonomyMode as PackAutonomyMode,
        Channel as PackChannel,
        ChannelConfig,
        TenantPack,
        VoiceRef,
    )
    from harness.hold import HoldRegistry
    import phase1_slice as slice_mod

    pack = TenantPack(
        tenant_id="zero-thr", display_name="zero-thr",
        voice=VoiceRef(skill="brand-voice/zero-thr"),
        channels={
            PackChannel.INSTAGRAM: ChannelConfig(
                autonomy=AutonomyConfig(mode=PackAutonomyMode.AUTO, threshold=0.0)
            )
        },
    )
    monkeypatch.setattr(slice_mod, "load_pack", lambda tenant_id, **kw: pack)

    result = asyncio.run(
        slice_mod.run_slice(
            tenant_id="zero-thr", topic="x", dsn="unused://",
            connector=object(),  # never reached on the review path
            assemble_model=_valid_then_error_model(_BRIEF_A, ConnectionError("down")),
            channel=PackChannel.INSTAGRAM,
            hold_registry=HoldRegistry().lift("zero-thr"),
        )
    )
    assert result.state.confidence is None
    assert result.decision.value == "review"  # NOT auto, even at threshold 0.0
    assert result.enqueue_status is None and result.dispatched == 0
