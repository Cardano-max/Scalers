"""Router unit tests (HARN-05): auto / review / regenerate + threshold edges.

Signature under test (systemdesign §6.2):
``route(confidence, threshold, gates, autonomy) -> "auto"|"review"|"regenerate"``.
Regenerate is gate-driven; confidence vs the single threshold splits auto/review.
"""

from __future__ import annotations

import pytest

from harness.router import DEFAULT_THRESHOLD, route
from harness.state import AutonomyMode, Gate, RouteDecision

PASS = Gate(name="length", passed=True)
FAIL = Gate(name="banned_phrase", passed=False, detail="contains banned term")


# --- confidence vs threshold: auto / review ---


def test_high_confidence_auto():
    assert route(0.95) is RouteDecision.AUTO


def test_below_threshold_is_review():
    assert route(0.7) is RouteDecision.REVIEW


def test_confidence_ceiling_one_autos():
    assert route(1.0) is RouteDecision.AUTO


def test_clean_gates_still_auto():
    assert route(0.95, gates=[PASS, PASS]) is RouteDecision.AUTO


# --- threshold edges (default threshold = 0.85, inclusive) ---


def test_just_below_threshold_is_review():
    assert route(0.8499) is RouteDecision.REVIEW


def test_exact_threshold_autos():
    # threshold is inclusive: confidence == threshold -> AUTO
    assert route(DEFAULT_THRESHOLD) is RouteDecision.AUTO


def test_custom_threshold_shifts_boundary():
    assert route(0.9, threshold=0.95) is RouteDecision.REVIEW
    assert route(0.95, threshold=0.95) is RouteDecision.AUTO


# --- gate-driven regenerate ---


def test_failed_gate_regenerates_even_at_high_confidence():
    assert route(0.99, gates=[PASS, FAIL]) is RouteDecision.REGENERATE


def test_failed_gate_takes_priority_over_review_band():
    assert route(0.1, gates=[FAIL]) is RouteDecision.REGENERATE


def test_regenerate_returns_the_literal_string():
    # The §6.2 contract is a Literal["auto","review","regenerate"]; the str-enum
    # member equals that literal.
    assert route(0.99, gates=[FAIL]) == "regenerate"


# --- autonomy dial ---


def test_review_mode_blocks_auto():
    assert route(0.95, autonomy=AutonomyMode.REVIEW) is RouteDecision.REVIEW


def test_review_mode_does_not_override_gate_regenerate():
    assert (
        route(0.95, gates=[FAIL], autonomy=AutonomyMode.REVIEW)
        is RouteDecision.REGENERATE
    )


# --- purity / determinism ---


def test_router_is_deterministic():
    decisions = {route(0.7) for _ in range(50)}
    assert decisions == {RouteDecision.REVIEW}


# --- validation ---


@pytest.mark.parametrize("bad", [-0.01, 1.01, 2.0, -5.0])
def test_out_of_range_confidence_raises(bad):
    with pytest.raises(ValueError):
        route(bad)


@pytest.mark.parametrize("bad", [-0.01, 1.5])
def test_out_of_range_threshold_raises(bad):
    with pytest.raises(ValueError):
        route(0.9, threshold=bad)
