"""Real-jury write path (AUTON-01 / 4jx.2) — produce_and_record_decision_real.

Exercises the end-to-end producer (run panel -> aggregate -> derive -> persist)
against the in-memory store with a deterministic injected panel runner.
"""

from __future__ import annotations

import asyncio

from autonomy.decision import EscKind, RouteDecision, SafetyVerdict
from autonomy.judges import JudgeScore, JudgeSpec
from autonomy.produce import produce_and_record_decision_real
from autonomy.store import InMemoryDecisionStore
from harness.state import AutonomyMode


def _runner(score_by_name):
    async def run(spec: JudgeSpec, action: str) -> JudgeScore:
        return score_by_name[spec.name]
    return run


def _all(**kw):
    base = dict(voice=0.95, safety=0.95, appr=0.95, on_voice=True)
    base.update(kw)
    return {n: JudgeScore(**base) for n in ("haiku-strict", "haiku-charitable", "ollama-cross")}


def _produce(store, runner, *, autonomy=AutonomyMode.AUTO, threshold=0.85, self_consistency=1.0):
    return asyncio.run(
        produce_and_record_decision_real(
            store,
            decision_id="d1", run_id="r1", tenant_id="ladies8391",
            channel="instagram", action_kind="post",
            action="a healed floral cover-up, captioned warmly",
            threshold=threshold, autonomy=autonomy, judge_runner=runner,
            self_consistency=self_consistency,
        )
    )


def test_real_jury_persists_cross_family_votes():
    store = InMemoryDecisionStore()
    rec = _produce(store, _runner(_all()))
    assert {v.family for v in rec.jury} >= {"anthropic", "ollama"}  # cross-family
    assert len(rec.jury) == 3
    assert store.get_decision("d1") == rec  # round-trips


def test_clean_high_panel_auto_when_unblocked():
    rec = _produce(InMemoryDecisionStore(), _runner(_all()))
    assert rec.decision is RouteDecision.AUTO and rec.esc.kind is EscKind.NONE


def test_uncomputable_confidence_routes_review():
    # self_consistency=None (probe couldn't run / too few samples) -> confidence
    # uncomputable -> fail safe to review even on a clean high panel in AUTO mode.
    # "couldn't compute" is never treated as high confidence.
    rec = _produce(InMemoryDecisionStore(), _runner(_all()), self_consistency=None)
    assert rec.decision is RouteDecision.REVIEW
    assert rec.self_consistency is None


def test_low_self_consistency_blocks_auto():
    # the jury liked its one sample, but the generator is unstable across probes
    # (low self-consistency) -> pooled confidence drops below threshold -> review.
    # this is what computed confidence buys over the flat 0.9: a wobbly generator
    # can't auto-fire even when the jury scored the single judged sample highly.
    rec = _produce(InMemoryDecisionStore(), _runner(_all()), self_consistency=0.2)
    assert rec.decision is RouteDecision.REVIEW
    assert rec.self_consistency == 0.2


def test_hard_fail_item_routes_review_via_floor():
    # exact-voice-but-inappropriate: high numbers, appr hard-fail -> the floor blocks.
    scores = _all(appr=0.95, appr_hard_fail=True)
    rec = _produce(InMemoryDecisionStore(), _runner(scores))
    assert rec.decision is RouteDecision.REVIEW
    assert rec.esc.kind is EscKind.GATE and "hard-fail" in rec.esc.label
    assert rec.pooled_confidence > 0.9  # high average; floor still blocked it


def test_held_channel_reviews_regardless():
    # autonomy=HOLD (b3f 439): the jury can never produce AUTO on a held channel.
    rec = _produce(InMemoryDecisionStore(), _runner(_all()), autonomy=AutonomyMode.HOLD)
    assert rec.decision is RouteDecision.REVIEW


def test_single_family_panel_is_refused():
    # Finding B: a misconfigured single-family panel must be refused, not silently
    # auto-fire on one family judging itself.
    import pytest

    single = (JudgeSpec("a", "anthropic", "anthropic:claude-haiku-4-5", "x"),
              JudgeSpec("b", "anthropic", "anthropic:claude-haiku-4-5", "y"))
    with pytest.raises(ValueError, match="cross-family"):
        asyncio.run(
            produce_and_record_decision_real(
                InMemoryDecisionStore(), decision_id="d", run_id="r", tenant_id="t",
                channel="instagram", action_kind="post", action="x",
                panel=single, judge_runner=_runner({"a": JudgeScore(voice=0.9, safety=0.9, appr=0.9, on_voice=True),
                                                     "b": JudgeScore(voice=0.9, safety=0.9, appr=0.9, on_voice=True)}),
            )
        )


def test_safety_veto_blocks_even_clean_panel():
    store = InMemoryDecisionStore()
    rec = asyncio.run(
        produce_and_record_decision_real(
            store, decision_id="d2", run_id="r1", tenant_id="t", channel="instagram",
            action_kind="post", action="x", threshold=0.85,
            safety_verdict=SafetyVerdict.VETO, judge_runner=_runner(_all()),
            self_consistency=1.0,
        )
    )
    assert rec.decision is RouteDecision.REVIEW and rec.esc.kind is EscKind.SAFETY
