"""Unit tests for the calibration/accuracy GATES (EVAL-03 / rvy.8). DB-free.

Covers: pass/fail at the bar, boundary inclusivity, skip-neutral (no data),
not-promotable (thin data / κ dependency), config-driven thresholds (not
hardcoded), per-commit vs per-promotion lanes, and the build-fail hook.
"""

from __future__ import annotations

import pytest

from evals.config import DEFAULT_GATES, ECE, PRECISION
from evals.gates import (
    GateFailed,
    GateReport,
    GateStatus,
    accuracy_gates,
    brand_voice_gates,
    calibration_gate,
)
from kb.schema import RunKind


def _statuses(outs):
    return {o.metric: o.status for o in outs}


# ── accuracy gates ───────────────────────────────────────────────────────────


def test_accuracy_passes_at_and_above_bar_inclusive():
    # 20/20 correct → P=R=1.0 ≥ 0.95.
    pairs = [("a", "a")] * 10 + [("b", "b")] * 10
    outs = accuracy_gates(pairs, cell="triage")
    assert _statuses(outs) == {"precision": GateStatus.PASS, "recall": GateStatus.PASS}


def test_accuracy_boundary_exactly_095_passes():
    # Construct macro precision == 0.95 exactly is fiddly; assert the boundary rule
    # directly via the EvalMetric used by the gate.
    from kb.schema import Direction, EvalMetric
    em = EvalMetric(metric="precision", value=0.95, threshold=0.95, direction=Direction.GTE)
    assert em.compute_passed() is True  # GTE inclusive
    em_ece = EvalMetric(metric="ece", value=0.05, threshold=0.05, direction=Direction.LTE)
    assert em_ece.compute_passed() is True  # LTE inclusive


def test_accuracy_fails_below_bar():
    # 3 wrong (pred a / expected b) drops macro P (a: 10/13=.77) and R (b: 7/10=.7)
    # clearly below 0.95.
    pairs = [("a", "a")] * 10 + [("b", "b")] * 7 + [("a", "b")] * 3
    outs = accuracy_gates(pairs, cell="triage")
    assert any(o.status is GateStatus.FAIL for o in outs)


def test_accuracy_no_data_is_skipped_not_failed():
    outs = accuracy_gates([], cell="not-built-yet")
    assert all(o.status is GateStatus.SKIPPED for o in outs)
    # skipped gates never break the build
    rep = GateReport(outs)
    rep.enforce_per_commit()  # must not raise


def test_accuracy_thin_data_is_not_promotable():
    outs = accuracy_gates([("a", "a"), ("b", "b")], cell="triage")  # n=2 < min 10
    assert all(o.status is GateStatus.NOT_PROMOTABLE for o in outs)


# ── calibration gate (ECE) ───────────────────────────────────────────────────


def test_calibration_pass_when_well_calibrated():
    pairs = [(1.0, True)] * 15 + [(0.0, False)] * 15  # ECE 0
    o = calibration_gate(pairs)
    assert o.status is GateStatus.PASS and o.run_kind is RunKind.PER_COMMIT


def test_calibration_fail_when_overconfident():
    # Spread is wide (0.1..0.9 → reliable) but badly miscalibrated: confident-and-
    # wrong + unconfident-and-right → ECE ≈ 0.8 ≫ 0.05.
    pairs = [(0.9, False)] * 20 + [(0.1, True)] * 20
    assert calibration_gate(pairs).status is GateStatus.FAIL


def test_calibration_thin_data_not_promotable():
    assert calibration_gate([(0.9, True), (0.1, False)]).status is GateStatus.NOT_PROMOTABLE


# ── brand-voice gates (κ dependency) ─────────────────────────────────────────


def test_brand_voice_passes_when_kappa_and_rate_clear():
    rater_pairs = [(True, True)] * 9 + [(False, False)]  # κ=1.0
    consensus = [True] * 10  # 100% on-voice
    outs = brand_voice_gates(rater_pairs, consensus)
    assert _statuses(outs) == {"kappa": GateStatus.PASS, "brand_voice_on_voice_rate": GateStatus.PASS}


def test_brand_voice_rate_not_promotable_when_kappa_fails():
    # Raters disagree at chance → κ≈0 < 0.6; even a high on-voice rate can't pass.
    rater_pairs = [(True, True), (True, False), (False, True), (False, False)] * 5
    consensus = [True] * 20  # 100% — but label quality is bad
    outs = brand_voice_gates(rater_pairs, consensus)
    st = _statuses(outs)
    assert st["kappa"] is GateStatus.FAIL
    assert st["brand_voice_on_voice_rate"] is GateStatus.NOT_PROMOTABLE


# ── config-driven (not hardcoded) + lanes ────────────────────────────────────


def test_thresholds_come_from_config_and_are_tightenable():
    # Default precision bar 0.95; tighten to 0.99 — thresholds come from config.
    base = DEFAULT_GATES
    tight = DEFAULT_GATES.tighten(PRECISION, 0.99)
    assert base.by_metric(PRECISION).threshold == 0.95
    assert tight.by_metric(PRECISION).threshold == 0.99
    assert base.by_metric(ECE).threshold == 0.05  # untouched


def test_per_commit_vs_per_promotion_split():
    pc = {g.metric for g in DEFAULT_GATES.per_commit()}
    pp = {g.metric for g in DEFAULT_GATES.per_promotion()}
    assert "ece" in pc and "precision" in pc and "recall" in pc
    assert "brand_voice_on_voice_rate" in pp and "kappa" in pp
    assert pc.isdisjoint(pp)


# ── build-fail wiring ────────────────────────────────────────────────────────


def test_enforce_per_commit_raises_only_on_real_fail():
    good = accuracy_gates([("a", "a")] * 10 + [("b", "b")] * 10, cell="ok")
    bad = accuracy_gates([("a", "a")] * 10 + [("a", "b")] * 10, cell="broken")  # recall 0 on b
    GateReport(good).enforce_per_commit()  # no raise
    with pytest.raises(GateFailed):
        GateReport(good + bad).enforce_per_commit()


def test_promotable_requires_all_pass():
    good = accuracy_gates([("a", "a")] * 10 + [("b", "b")] * 10, cell="ok")
    skipped = accuracy_gates([], cell="later")
    assert GateReport(good).promotable() is True
    assert GateReport(good + skipped).promotable() is False  # a skip blocks promotion
