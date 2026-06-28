"""Unit tests for the eval metric math (EVAL-03 / rvy.8). DB-free, deterministic."""

from __future__ import annotations

import pytest

from evals.metrics import (
    classification_prf,
    cohens_kappa,
    expected_calibration_error,
    extraction_prf,
    on_voice_rate,
)

# ── ECE ──────────────────────────────────────────────────────────────────────


def test_ece_perfectly_calibrated_is_zero():
    # Confidence exactly matches accuracy in every bin → ECE 0.
    pairs = [(1.0, True)] * 15 + [(0.0, False)] * 15  # spread=1.0, n=30
    r = expected_calibration_error(pairs)
    assert r.value == pytest.approx(0.0)
    assert r.reliable is True


def test_ece_overconfident_is_large():
    # Always 90% confident but only 50% correct → ECE ≈ 0.4.
    pairs = [(0.9, i % 2 == 0) for i in range(40)]
    r = expected_calibration_error(pairs)
    assert r.value == pytest.approx(0.4, abs=0.05)


def test_ece_insufficient_sample_is_unreliable():
    r = expected_calibration_error([(0.9, True), (0.1, False)], min_samples=20)
    assert r.reliable is False
    assert "insufficient" in r.detail["reason"]


def test_ece_narrow_confidence_range_is_unreliable():
    # 30 samples but every confidence in [0.80, 0.82] → range too narrow to trust.
    pairs = [(0.80 + (i % 3) * 0.01, True) for i in range(30)]
    r = expected_calibration_error(pairs, min_confidence_spread=0.1)
    assert r.reliable is False
    assert "narrow" in r.detail["reason"]


def test_ece_rejects_out_of_range_confidence():
    with pytest.raises(ValueError):
        expected_calibration_error([(1.5, True)])


# ── classification P/R/F1 ────────────────────────────────────────────────────


def test_classification_perfect():
    pairs = [("a", "a"), ("b", "b"), ("a", "a"), ("b", "b")] * 3  # n=12
    r = classification_prf(pairs)
    assert r.detail["precision"] == 1.0 and r.detail["recall"] == 1.0
    assert r.value == 1.0  # macro F1


def test_classification_macro_penalizes_minority_class_errors():
    # Majority class perfect, minority class always misclassified → macro < micro.
    pairs = [("maj", "maj")] * 18 + [("maj", "min"), ("maj", "min")]  # min recall 0
    r = classification_prf(pairs)
    assert r.detail["per_class"]["min"]["recall"] == 0.0
    assert r.value < 0.95  # macro F1 drops; a gate at 0.95 would catch it


def test_classification_insufficient_sample():
    r = classification_prf([("a", "a")], min_samples=10)
    assert r.reliable is False


# ── extraction field-level P/R ───────────────────────────────────────────────


def test_extraction_counts_fields_correctly():
    # ex1: name correct, company wrong → 1 TP, 1 FP(company pred), 1 FN(company exp)
    # ex2: both correct → 2 TP
    pairs = [
        ({"name": "Sam", "company": "WRONG"}, {"name": "Sam", "company": "Acme"}),
        ({"name": "Lee", "role": "CTO"}, {"name": "Lee", "role": "CTO"}),
    ]
    r = extraction_prf(pairs, min_samples=1)
    assert r.detail["tp"] == 3 and r.detail["fp"] == 1 and r.detail["fn"] == 1


def test_extraction_missing_field_is_recall_miss():
    pairs = [({"name": "Sam"}, {"name": "Sam", "company": "Acme"})] * 10
    r = extraction_prf(pairs)
    assert r.detail["recall"] < 1.0 and r.detail["precision"] == 1.0


# ── Cohen's kappa ────────────────────────────────────────────────────────────


def test_kappa_perfect_agreement():
    pairs = [(True, True), (False, False)] * 10
    assert cohens_kappa(pairs).value == pytest.approx(1.0)


def test_kappa_chance_agreement_near_zero():
    # Raters independent 50/50 → κ ≈ 0.
    pairs = [(True, True), (True, False), (False, True), (False, False)] * 10
    assert cohens_kappa(pairs).value == pytest.approx(0.0, abs=0.05)


def test_kappa_single_value_degenerate_is_one():
    # Both raters always say True (pe==1) → defined as κ=1.0, not div-by-zero.
    assert cohens_kappa([(True, True)] * 10).value == 1.0


# ── on-voice rate ────────────────────────────────────────────────────────────


def test_on_voice_rate():
    labels = [True] * 9 + [False]  # 90%
    r = on_voice_rate(labels)
    assert r.value == pytest.approx(0.9)
    assert r.detail["on_voice"] == 9


def test_empty_inputs_are_unreliable_not_crash():
    for r in (expected_calibration_error([]), classification_prf([]),
              extraction_prf([]), cohens_kappa([]), on_voice_rate([])):
        assert r.n == 0 and r.reliable is False
