"""Deterministic jury aggregation + hard-fail floor (AUTON-01 / 4jx.2) — DB-free.

The safety-critical heart: reliability-weighted per-dimension means, REAL per-
dimension agreement (not the stub's 1.0), and the hard-fail FLOOR that a high score
on another dimension can never wash out.
"""

from __future__ import annotations

import pytest

from autonomy.aggregate import aggregate_jury
from autonomy.decision import (
    AGREEMENT_MIN,
    EscKind,
    JudgeVote,
    RouteDecision,
    SafetyVerdict,
    derive_decision,
)


def _v(judge, *, voice=0.9, safety=0.9, appr=0.9, weight=1.0, vhf=False, shf=False, ahf=False, family="anthropic"):
    return JudgeVote(
        judge=judge, family=family, voice=voice, safety=safety, appr=appr,
        reliability_weight=weight, voice_hard_fail=vhf, safety_hard_fail=shf, appr_hard_fail=ahf,
    )


# ── reliability-weighted per-dimension mean ──────────────────────────────────


def test_uniform_weight_is_plain_mean():
    agg = aggregate_jury([_v("a", voice=0.8), _v("b", voice=0.6)])
    assert agg.dim_score["voice"] == pytest.approx(0.7)


def test_reliability_weight_downweights_a_judge():
    # b is half-weight, so its 0.6 counts half as much: (1*0.8 + 0.5*0.6)/1.5.
    agg = aggregate_jury([_v("a", voice=0.8, weight=1.0), _v("b", voice=0.6, weight=0.5)])
    assert agg.dim_score["voice"] == pytest.approx((0.8 + 0.5 * 0.6) / 1.5)


def test_zero_total_weight_yields_no_signal():
    agg = aggregate_jury([_v("a", voice=0.9, weight=0.0)])
    assert agg.dim_score["voice"] == 0.0  # no signal, not a passing default


def test_pooled_is_mean_of_dimension_scores():
    agg = aggregate_jury([_v("a", voice=0.9, safety=0.6, appr=0.3)])
    assert agg.pooled == pytest.approx((0.9 + 0.6 + 0.3) / 3)


# ── real per-dimension agreement (not hardcoded 1.0) ─────────────────────────


def test_unanimous_agreement_is_one():
    agg = aggregate_jury([_v("a", voice=0.9), _v("b", voice=0.9)])
    assert agg.agreement["voice"] == 1.0


def test_divergent_scores_drop_agreement_below_one():
    agg = aggregate_jury([_v("a", appr=0.95), _v("b", appr=0.35)])
    assert agg.agreement["appr"] == pytest.approx(1.0 - 0.6)
    assert agg.worst_agreement == pytest.approx(0.4)


def test_worst_dimension_drives_agreement():
    # voice unanimous, appr split -> the worst (appr) is the panel's agreement.
    agg = aggregate_jury([_v("a", voice=0.9, appr=0.9), _v("b", voice=0.9, appr=0.2)])
    assert agg.agreement["voice"] == 1.0
    assert agg.worst_agreement == pytest.approx(1.0 - 0.7)


def test_empty_panel_is_no_signal_not_agreement():
    agg = aggregate_jury([])
    assert agg.pooled == 0.0 and agg.worst_agreement == 0.0 and agg.n_judges == 0


# ── hard-fail FLOOR: separate from the mean, any judge, any dimension ─────────


def test_hard_fail_is_any_judge_per_dimension():
    agg = aggregate_jury([_v("a", ahf=False), _v("b", ahf=True)])
    assert agg.hard_fail["appr"] is True
    assert agg.hard_fail["voice"] is False
    assert agg.any_hard_fail and agg.hard_fail_dims == frozenset({"appr"})


def test_hard_fail_not_averaged_into_score():
    # The appr score stays HIGH (the disqualifier is NOT a low number in the mean) —
    # the floor lives outside the average, as a separate boolean.
    agg = aggregate_jury([_v("a", appr=0.95, ahf=True)])
    assert agg.dim_score["appr"] == pytest.approx(0.95)
    assert agg.hard_fail["appr"] is True


# ── derive_decision with the real aggregate ──────────────────────────────────


def _decide(votes, **kw):
    agg = aggregate_jury(votes)
    return derive_decision(votes=votes, aggregate=agg, threshold=0.85, **kw), agg


def test_hard_fail_forces_review_even_at_high_confidence():
    # Exact-voice-but-inappropriate: voice≈1, appr≈1 numerically, but an appr
    # hard-fail tag -> REVIEW. A high aggregate can never auto-fire over the floor.
    votes = [_v("a", voice=0.98, safety=0.98, appr=0.98, ahf=True),
             _v("b", voice=0.97, safety=0.97, appr=0.97, ahf=True)]
    (decision, esc, pooled, agree), agg = _decide(votes)
    assert decision is RouteDecision.REVIEW
    assert esc.kind is EscKind.GATE and "hard-fail" in esc.label and "appr" in esc.label
    assert pooled > 0.95  # the average was high; the floor still blocked it


def test_clean_unanimous_high_panel_can_auto():
    # On the measured path AUTO also requires a COMPUTED confidence (4jx.3): jury
    # quality alone must not clear the bar. Supply one, as the real producer does.
    votes = [_v("a", voice=0.95, safety=0.95, appr=0.95), _v("b", voice=0.95, safety=0.95, appr=0.95)]
    (decision, esc, pooled, agree), _ = _decide(votes, confidence=0.95)
    assert decision is RouteDecision.AUTO and esc.kind is EscKind.NONE


def test_measured_path_without_computed_confidence_cannot_auto():
    """REGRESSION (adversarial hardening): a caller on the aggregate path that
    drops the computed confidence (confidence=None, uncomputable flag unset) must
    fail safe to review — never silently fall back to jury-only pooling."""
    votes = [_v("a", voice=0.95, safety=0.95, appr=0.95), _v("b", voice=0.95, safety=0.95, appr=0.95)]
    (decision, esc, _, _), _ = _decide(votes)  # no confidence kwarg at all
    assert decision is RouteDecision.REVIEW
    # arch/#93 label split: caller-omission reads distinctly from probe-starvation.
    assert "not supplied (measured path)" in esc.label


def test_probe_starvation_label_is_distinct():
    # The OTHER trigger (explicit uncomputable flag) keeps its own honest label.
    votes = [_v("a", voice=0.95, safety=0.95, appr=0.95), _v("b", voice=0.95, safety=0.95, appr=0.95)]
    (decision, esc, _, _), _ = _decide(votes, confidence=None, confidence_uncomputable=True)
    assert decision is RouteDecision.REVIEW
    assert "uncomputable (insufficient samples)" in esc.label


def test_split_panel_routes_review():
    votes = [_v("a", voice=0.95, safety=0.95, appr=0.95), _v("b", voice=0.95, safety=0.95, appr=0.2)]
    (decision, esc, pooled, agree), _ = _decide(votes)
    assert decision is RouteDecision.REVIEW and esc.kind is EscKind.SPLIT
    assert agree < AGREEMENT_MIN


def test_safety_veto_precedes_hard_fail():
    # Precedence: a safety VETO is reported before the rubric hard-fail.
    votes = [_v("a", appr=0.9, ahf=True)]
    (decision, esc, _, _), _ = _decide(votes, safety_verdict=SafetyVerdict.VETO)
    assert decision is RouteDecision.REVIEW and esc.kind is EscKind.SAFETY


def test_hard_fail_precedes_split_and_threshold():
    # Both a hard-fail AND a split present -> hard-fail (GATE) is reported first.
    votes = [_v("a", voice=0.95, appr=0.95, ahf=True), _v("b", voice=0.2, appr=0.2, ahf=False)]
    (decision, esc, _, _), _ = _decide(votes)
    assert esc.kind is EscKind.GATE and "hard-fail" in esc.label


# ── adversarial-review backstops (Findings A + B) ────────────────────────────


def test_hard_fail_floor_holds_without_an_aggregate():
    # Finding A: a caller that passes REAL votes but no aggregate must still be
    # blocked by the floor (the votes' own per-dim flags are the backstop).
    votes = [_v("a", voice=0.98, safety=0.98, appr=0.98, ahf=True),
             _v("b", voice=0.98, safety=0.98, appr=0.98, ahf=True)]
    decision, esc, _, _ = derive_decision(votes=votes, threshold=0.85)  # NO aggregate
    assert decision is RouteDecision.REVIEW and esc.kind is EscKind.GATE
    assert "hard-fail" in esc.label


def test_single_judge_panel_cannot_auto():
    # Finding B: one clean high judge, expected=1 -> still REVIEW (can't measure
    # agreement on a lone juror; never auto-fire on one voice).
    votes = [_v("solo", voice=0.97, safety=0.97, appr=0.97)]
    agg = aggregate_jury(votes)
    decision, esc, _, _ = derive_decision(votes=votes, aggregate=agg, threshold=0.85, expected_judges=1)
    assert decision is RouteDecision.REVIEW and esc.kind is EscKind.DEGRADED
    assert "insufficient jury" in esc.label
