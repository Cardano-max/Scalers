"""Computed confidence (AUTON-02 / 4jx.3) — DB-free.

Self-consistency over probe samples, conservative pooling with jury quality, the
calibration map, and the uncomputable fail-safe. NO logprobs anywhere.
"""

from __future__ import annotations

import asyncio

import pytest

from autonomy.confidence import (
    Calibration,
    compute_confidence,
    ece,
    probe_self_consistency,
    self_consistency,
)


# ── self-consistency (modal agreement over probe samples) ────────────────────


def test_unanimous_samples_full_consistency():
    assert self_consistency(["a", "a", "a", "a"]) == 1.0


def test_high_variance_lowers_consistency():
    # 4 distinct answers in 5 samples -> low self-consistency.
    low = self_consistency(["a", "b", "c", "d", "a"])
    high = self_consistency(["a", "a", "a", "b", "a"])
    assert low < high
    assert low == pytest.approx(2 / 5) and high == pytest.approx(4 / 5)


def test_too_few_samples_is_none_fail_safe():
    assert self_consistency(["a", "a"], min_samples=3) is None
    assert self_consistency([]) is None


def test_probe_runs_k_samples_and_drops_errors():
    calls = {"n": 0}

    async def sampler():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("flaky probe")
        return "draft-X"  # everything agrees except the dropped one

    sc = asyncio.run(probe_self_consistency(sampler, k=5))
    assert calls["n"] == 5
    assert sc == 1.0  # 4 successful, all agree (the error sample dropped, not faked)


def test_probe_too_few_successes_is_none():
    async def sampler():
        raise ConnectionError("probe down")

    assert asyncio.run(probe_self_consistency(sampler, k=5)) is None


# ── conservative pooling ─────────────────────────────────────────────────────


def test_low_self_consistency_pulls_confidence_down():
    high = compute_confidence(jury_quality=0.95, self_consistency_score=0.95).confidence
    low = compute_confidence(jury_quality=0.95, self_consistency_score=0.2).confidence
    assert low < high  # a wobbly generator drags confidence down even if jury liked it
    assert low == pytest.approx((0.95 + 0.2) / 2)


def test_uncomputable_when_self_consistency_none():
    res = compute_confidence(jury_quality=0.95, self_consistency_score=None)
    assert res.uncomputable and res.confidence is None


def test_components_carried_for_audit():
    res = compute_confidence(jury_quality=0.8, self_consistency_score=0.6)
    assert res.self_consistency == 0.6 and res.jury_quality == 0.8
    assert res.components["raw"] == pytest.approx(0.7)


# ── calibration + ECE (rvy.8 metric reused) ──────────────────────────────────


def test_identity_calibration_is_passthrough():
    assert Calibration().apply(0.73) == pytest.approx(0.73)


def test_fit_calibration_remaps_to_empirical_accuracy():
    # raw confidences ~0.9 but only half are correct -> calibrated down toward 0.5.
    pairs = [(0.92, i % 2 == 0) for i in range(20)]
    cal = Calibration.fit(pairs, n_bins=10)
    assert cal.apply(0.92) == pytest.approx(0.5, abs=0.15)


def test_ece_metric_is_the_eval_metric():
    # Reuses evals.expected_calibration_error so the gate + the computed-confidence
    # ECE are one computation. Perfectly-calibrated pairs -> low ECE.
    pairs = [(0.0, False)] * 10 + [(1.0, True)] * 10
    res = ece(pairs)
    assert res.value <= 0.05
