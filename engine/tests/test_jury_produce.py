"""Real-jury write path (AUTON-01 / 4jx.2) — produce_and_record_decision_real.

Exercises the end-to-end producer (run panel -> aggregate -> derive -> persist)
against the in-memory store with a deterministic injected panel runner.
"""

from __future__ import annotations

import asyncio

import pytest

from autonomy.confidence import IDENTITY_CALIBRATION, PROVENANCE_COMPUTED, Calibration
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


def _produce(store, runner, *, autonomy=AutonomyMode.AUTO, threshold=0.85, self_consistency=1.0,
             calibration=IDENTITY_CALIBRATION):
    return asyncio.run(
        produce_and_record_decision_real(
            store,
            decision_id="d1", run_id="r1", tenant_id="ladies8391",
            channel="instagram", action_kind="post",
            action="a healed floral cover-up, captioned warmly",
            threshold=threshold, autonomy=autonomy, judge_runner=runner,
            self_consistency=self_consistency, calibration=calibration,
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


def test_unmeasured_calibration_bin_at_threshold_routes_review_end_to_end():
    """4jx.15 AC1: extremes-only gold (ECE green) leaves the 0.8–0.9 bin unmeasured;
    a clean high panel (jury 0.95) + sc 0.84 pools raw to ~0.895, which previously
    passed through identity and routed AUTO (capped 0.84 >= 0.80) with ZERO
    calibration evidence. The unmeasured bin at/above the channel threshold is
    uncomputable -> REVIEW (see the measured-bin control below: same signals AUTO)."""
    cal = Calibration.fit([(0.05, False)] * 5 + [(0.95, True)] * 5, n_bins=10)
    rec = _produce(InMemoryDecisionStore(), _runner(_all()),
                   threshold=0.80, self_consistency=0.84, calibration=cal)
    assert rec.decision is RouteDecision.REVIEW
    assert "unmeasured" in rec.esc.label


def test_measured_bin_same_signals_still_auto():
    # Control for AC1: IDENTICAL signals, but the raw's bin is MEASURED (acc 1.0)
    # -> the rule does not fire and the channel autos. Measurement is the only delta.
    cal = Calibration.fit([(0.85, True)] * 10, n_bins=10)
    rec = _produce(InMemoryDecisionStore(), _runner(_all()),
                   threshold=0.80, self_consistency=0.84, calibration=cal)
    assert rec.decision is RouteDecision.AUTO


def test_real_path_persists_components_and_provenance():
    """4jx.17 AC1+AC2: the decision carries confidence_components (raw / p_est /
    jury_quality / self_consistency / cap_bind_delta) and the producer provenance
    tag — the LiftController's per-channel signal and the rvy.8 offline
    recompute's p_est source (pooled_confidence stores the CAPPED value)."""
    store = InMemoryDecisionStore()
    rec = _produce(store, _runner(_all()), self_consistency=0.9)
    assert rec.confidence_provenance == PROVENANCE_COMPUTED
    comps = rec.confidence_components
    assert comps is not None
    assert set(comps) == {"raw", "p_est", "jury_quality", "self_consistency", "cap_bind_delta"}
    # jury 0.95 + sc 0.9 -> raw/p_est 0.925 (identity map), routed capped to 0.9
    assert comps["p_est"] == pytest.approx(0.925)
    assert rec.pooled_confidence == pytest.approx(0.9)
    assert comps["cap_bind_delta"] == pytest.approx(0.025)
    assert store.get_decision("d1").confidence_components == comps  # round-trips


def test_uncomputable_decision_still_carries_provenance():
    # Components are None (nothing computed) but the producer identity remains.
    rec = _produce(InMemoryDecisionStore(), _runner(_all()), self_consistency=None)
    assert rec.confidence_provenance == PROVENANCE_COMPUTED
    assert rec.confidence_components is None


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
