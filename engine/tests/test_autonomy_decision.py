"""Unit tests for autonomy decision derivation + jury stub (OBS-02)."""

from __future__ import annotations

import pytest

from autonomy.decision import (
    AGREEMENT_MIN,
    EscKind,
    JudgeVote,
    SafetyVerdict,
    agreement,
    derive_decision,
    pool_confidence,
)
from autonomy.jury import JURY_PANEL, expected_judge_count, stub_jury
from harness.state import AutonomyMode, Gate, RouteDecision


def _votes(*overalls: float) -> list[JudgeVote]:
    # Each juror scores all three dimensions equal to its overall.
    return [JudgeVote(judge=f"j{i}", voice=o, safety=o, appr=o) for i, o in enumerate(overalls)]


# -- pooling + agreement ---------------------------------------------------- #


def test_pool_confidence_is_mean_of_overalls():
    assert pool_confidence(_votes(0.9, 0.9, 0.6)) == pytest.approx((0.9 + 0.9 + 0.6) / 3)


def test_pool_confidence_empty_is_zero():
    assert pool_confidence([]) == 0.0


def test_agreement_unanimous_is_one():
    assert agreement(_votes(0.8, 0.8, 0.8)) == 1.0


def test_agreement_drops_with_spread():
    assert agreement(_votes(0.9, 0.4)) == pytest.approx(0.5)


def test_single_juror_agreement_is_one():
    assert agreement(_votes(0.7)) == 1.0


# -- derive_decision: escalation precedence --------------------------------- #


def test_auto_when_clean_and_confident():
    d, esc, pooled, agree = derive_decision(votes=_votes(0.9, 0.9, 0.9), threshold=0.85)
    assert d is RouteDecision.AUTO
    assert esc.kind is EscKind.NONE
    assert pooled == pytest.approx(0.9)
    assert agree == 1.0


def test_gate_failure_regenerates_and_takes_precedence():
    # Even with perfect jury, a failed gate -> regenerate (esc=gate).
    gates = [Gate(name="banned_phrase", passed=False)]
    d, esc, *_ = derive_decision(votes=_votes(1.0, 1.0, 1.0), threshold=0.85, gates=gates)
    assert d is RouteDecision.REGENERATE
    assert esc.kind is EscKind.GATE


def test_safety_veto_blocks_auto():
    d, esc, *_ = derive_decision(
        votes=_votes(0.95, 0.95, 0.95), threshold=0.85, safety_verdict=SafetyVerdict.VETO
    )
    assert d is RouteDecision.REVIEW
    assert esc.kind is EscKind.SAFETY


def test_jury_split_blocks_auto_even_above_threshold():
    # Pooled 0.7 (>= a low bar) but jurors span 0.95..0.2 -> agreement < min.
    votes = _votes(0.95, 0.2)  # agreement = 1 - 0.75 = 0.25 < 0.5
    d, esc, pooled, agree = derive_decision(votes=votes, threshold=0.5)
    assert agree < AGREEMENT_MIN
    assert d is RouteDecision.REVIEW
    assert esc.kind is EscKind.SPLIT


def test_degraded_when_fewer_judges_than_expected():
    d, esc, *_ = derive_decision(
        votes=_votes(0.9, 0.9), threshold=0.85, expected_judges=4
    )
    assert d is RouteDecision.REVIEW
    assert esc.kind is EscKind.DEGRADED


def test_below_threshold_reviews():
    d, esc, *_ = derive_decision(votes=_votes(0.6, 0.6, 0.6), threshold=0.85)
    assert d is RouteDecision.REVIEW
    assert esc.kind is EscKind.BELOW_THRESHOLD


def test_threshold_boundary_is_inclusive_auto():
    # Exactly at the bar auto-fires (matches the router's inclusive auto bar).
    d, esc, *_ = derive_decision(votes=_votes(0.85, 0.85, 0.85), threshold=0.85)
    assert d is RouteDecision.AUTO
    assert esc.kind is EscKind.NONE


def test_autonomy_mode_review_forces_review():
    d, esc, *_ = derive_decision(
        votes=_votes(0.95, 0.95, 0.95), threshold=0.85, autonomy=AutonomyMode.REVIEW
    )
    assert d is RouteDecision.REVIEW
    assert esc.kind is EscKind.MODE


def test_safety_precedence_over_split():
    # Both a safety veto and a split present -> safety wins (more severe).
    d, esc, *_ = derive_decision(
        votes=_votes(0.9, 0.2), threshold=0.5, safety_verdict=SafetyVerdict.VETO
    )
    assert esc.kind is EscKind.SAFETY


# -- stub jury -------------------------------------------------------------- #


def test_stub_jury_one_vote_per_panel_judge():
    votes = stub_jury(0.9)
    assert len(votes) == expected_judge_count() == len(JURY_PANEL)
    assert [v.judge for v in votes] == [j for j, _ in JURY_PANEL]


def test_stub_jury_is_cross_family():
    families = {v.family for v in stub_jury(0.9)}
    # At least one non-anthropic juror so a family never judges itself.
    assert "anthropic" in families
    assert len(families) >= 2


def test_stub_jury_deterministic_and_clamped():
    assert stub_jury(0.7) == stub_jury(0.7)
    assert all(v.voice == 1.0 for v in stub_jury(1.5))   # clamped high
    assert all(v.voice == 0.0 for v in stub_jury(-1.0))  # clamped low
