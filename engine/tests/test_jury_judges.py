"""Real jury panel + orchestration (AUTON-01 / 4jx.2) — DB-free, hermetic.

Drives the panel through an injected ``judge_runner`` (deterministic, records the
calls) so the cross-family orchestration + edge cases run without live models, and
exercises one judge cell through a ``FunctionModel`` to prove the typed cell path.
"""

from __future__ import annotations

import asyncio

from autonomy.aggregate import aggregate_jury
from autonomy.decision import EscKind, RouteDecision, derive_decision
from autonomy.judges import (
    DEFAULT_PANEL,
    JudgeScore,
    JudgeSpec,
    build_judge_cell,
    expected_judge_count,
    is_cross_family,
    panel_families,
    run_jury,
)
from tests.conftest import tool_model


def _score(voice=0.9, safety=0.9, appr=0.9, on_voice=True, vhf=False, shf=False, ahf=False):
    return JudgeScore(
        voice=voice, safety=safety, appr=appr, on_voice=on_voice,
        voice_hard_fail=vhf, safety_hard_fail=shf, appr_hard_fail=ahf,
    )


def _runner(score_by_name, *, calls=None):
    async def run(spec: JudgeSpec, action: str) -> JudgeScore:
        if calls is not None:
            calls.append(spec.name)
        return score_by_name[spec.name]
    return run


# ── cross-family by construction (only-Anthropic-key edge) ───────────────────


def test_default_panel_is_cross_family_via_ollama():
    fams = panel_families(DEFAULT_PANEL)
    assert is_cross_family(DEFAULT_PANEL)
    assert "anthropic" in fams and "ollama" in fams  # cross-family w/o any extra key
    assert expected_judge_count(DEFAULT_PANEL) >= 3


# ── real judges are actually invoked (not the always-agree stub) ─────────────


def test_every_seat_is_invoked_and_voted():
    calls: list[str] = []
    scores = {s.name: _score() for s in DEFAULT_PANEL}
    run = asyncio.run(run_jury("a post", panel=DEFAULT_PANEL, judge_runner=_runner(scores, calls=calls)))
    assert sorted(calls) == sorted(s.name for s in DEFAULT_PANEL)  # each judge called
    assert len(run.votes) == len(DEFAULT_PANEL) and not run.dropped
    assert {v.family for v in run.votes} >= {"anthropic", "ollama"}


# ── real divergence -> agreement < 1.0 (the stub artifact is gone) ───────────


def test_divergent_judges_yield_agreement_below_one():
    scores = {
        "opus-strict": _score(appr=0.35),
        "opus-charitable": _score(appr=0.95),
        "ollama-cross": _score(appr=0.6),
    }
    run = asyncio.run(run_jury("x", judge_runner=_runner(scores)))
    agg = aggregate_jury(run.votes)
    assert agg.agreement["appr"] < 1.0
    assert agg.worst_agreement < 1.0


def test_exact_voice_but_inappropriate_splits_dimensions():
    # The mastectomy-as-glow-up case: voice high, appropriateness low + hard-fail.
    scores = {
        "opus-strict": _score(voice=0.95, safety=0.9, appr=0.15, ahf=True),
        "opus-charitable": _score(voice=0.96, safety=0.9, appr=0.2, ahf=True),
        "ollama-cross": _score(voice=0.94, safety=0.9, appr=0.18, ahf=True),
    }
    run = asyncio.run(run_jury("x", judge_runner=_runner(scores)))
    agg = aggregate_jury(run.votes)
    assert agg.dim_score["voice"] > 0.9 and agg.dim_score["appr"] < 0.3
    assert agg.hard_fail["appr"] is True
    decision, esc, _, _ = derive_decision(votes=run.votes, aggregate=agg, threshold=0.85)
    assert decision is RouteDecision.REVIEW and esc.kind is EscKind.GATE  # the floor fired


# ── a judge that errors / times out is DROPPED (no fake agreement) ───────────


def test_errored_judge_is_dropped_not_counted():
    scores = {"opus-strict": _score(), "ollama-cross": _score()}

    async def run(spec, action):
        if spec.name == "opus-charitable":
            raise RuntimeError("judge refused")
        return scores[spec.name]

    out = asyncio.run(run_jury("x", judge_runner=run))
    assert len(out.votes) == len(DEFAULT_PANEL) - 1
    assert ("opus-charitable", "RuntimeError: judge refused") in out.dropped
    assert out.expected_judges == len(DEFAULT_PANEL)  # coverage gap is visible
    # a degraded panel still routes review even if the survivors agree.
    agg = aggregate_jury(out.votes)
    decision, esc, _, _ = derive_decision(
        votes=out.votes, aggregate=agg, threshold=0.85, expected_judges=out.expected_judges
    )
    assert decision is RouteDecision.REVIEW and esc.kind is EscKind.DEGRADED


def test_timed_out_judge_is_dropped():
    async def run(spec, action):
        if spec.name == "ollama-cross":
            await asyncio.sleep(0.2)  # exceeds the tight timeout below
        return _score()

    out = asyncio.run(run_jury("x", judge_runner=run, timeout_s=0.05))
    assert ("ollama-cross", "timeout") in out.dropped
    assert all(v.judge != "ollama-cross" for v in out.votes)


def test_all_judges_down_yields_no_votes_fail_safe():
    async def run(spec, action):
        raise ConnectionError("model unavailable")

    out = asyncio.run(run_jury("x", judge_runner=run))
    assert out.votes == [] and len(out.dropped) == len(DEFAULT_PANEL)
    # empty panel -> no signal -> the decision layer fails safe to review.
    agg = aggregate_jury(out.votes)
    assert agg.pooled == 0.0 and agg.worst_agreement == 0.0
    decision, _, _, _ = derive_decision(votes=out.votes, aggregate=agg, threshold=0.85)
    assert decision is RouteDecision.REVIEW


# ── the typed judge cell runs through a FunctionModel ────────────────────────


def test_judge_cell_emits_typed_score():
    cell = build_judge_cell(DEFAULT_PANEL[0])
    payload = {"voice": 0.8, "safety": 0.9, "appr": 0.2, "on_voice": True, "appr_hard_fail": True}
    out = cell.run_sync("Score this post: ...", model=tool_model(payload))
    assert isinstance(out, JudgeScore)
    assert out.appr == 0.2 and out.appr_hard_fail is True and out.on_voice is True
