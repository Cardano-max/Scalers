"""Computed confidence (AUTON-02 / 4jx.3) — DB-free.

Self-consistency over probe samples, conservative pooling with jury quality, the
calibration map, and the uncomputable fail-safe. NO logprobs anywhere.
"""

from __future__ import annotations

import asyncio

import pytest

from autonomy.confidence import (
    Calibration,
    anchored_self_consistency,
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


# ── anchored self-consistency (the shipped sample is the reference) ──────────


def test_anchored_probes_agreeing_with_shipped_sample():
    assert anchored_self_consistency("a", ["a", "a"]) == 1.0
    assert anchored_self_consistency("a", ["a", "b"]) == 0.5


def test_anchored_probes_contradicting_shipped_sample_read_zero():
    # REGRESSION (adversarial): probes agreeing with EACH OTHER but not with the
    # shipped sample must read LOW — modal scoring rated [A,B,B] at 2/3.
    assert anchored_self_consistency("a", ["b", "b"]) == 0.0


def test_anchored_too_few_probes_is_none():
    assert anchored_self_consistency("a", ["a"]) is None
    assert anchored_self_consistency("a", []) is None


# ── conservative pooling (weakest-component cap) ─────────────────────────────


def test_low_self_consistency_pulls_confidence_down():
    high = compute_confidence(jury_quality=0.95, self_consistency_score=0.95).confidence
    low = compute_confidence(jury_quality=0.95, self_consistency_score=0.2).confidence
    assert low < high  # a wobbly generator drags confidence down even if jury liked it
    assert low == pytest.approx(0.2)  # capped at the weakest component, not the mean


def test_high_self_consistency_cannot_lift_below_bar_jury():
    """REGRESSION (adversarial, verified-by-execution repro): jury 0.80 + sc 1.0
    blended to 0.90 >= 0.85 -> a stable-but-mediocre generator auto-fired. The
    weakest-component cap pins confidence at 0.80 (the jury's own bar verdict)."""
    res = compute_confidence(jury_quality=0.80, self_consistency_score=1.0)
    assert res.confidence == pytest.approx(0.80)  # NOT 0.90
    assert res.components["raw"] == pytest.approx(0.90)  # the mean is still audited


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


def test_unmeasured_bin_is_identity_never_rounds_up():
    """REGRESSION (adversarial): gold pairs only at the extremes left the middle
    bins empty, and an empty bin mapped to its UPPER edge — apply(0.81) returned
    0.9, turning a below-bar raw into an above-bar calibrated (REVIEW -> AUTO).
    An unmeasured region must pass the raw value through unchanged."""
    cal = Calibration.fit([(0.05, False)] * 5 + [(0.95, True)] * 5, n_bins=10)
    assert cal.apply(0.81) == pytest.approx(0.81)  # identity, NOT 0.9
    assert cal.apply(0.42) == pytest.approx(0.42)  # any unmeasured bin
    # measured bins still remap to their empirical accuracy.
    assert cal.apply(0.95) == pytest.approx(1.0)
    assert cal.apply(0.05) == pytest.approx(0.0)


def test_ece_metric_is_the_eval_metric():
    # Reuses evals.expected_calibration_error so the gate + the computed-confidence
    # ECE are one computation. Perfectly-calibrated pairs -> low ECE.
    pairs = [(0.0, False)] * 10 + [(1.0, True)] * 10
    res = ece(pairs)
    assert res.value <= 0.05


# ── finite-input guards (4jx.14): NaN/inf -> uncomputable -> review ──────────


def test_nonfinite_inputs_are_uncomputable_each_position():
    nan, inf = float("nan"), float("inf")
    for jq, sc in [(nan, 1.0), (inf, 1.0), (1.0, nan), (1.0, inf), (nan, nan)]:
        res = compute_confidence(jury_quality=jq, self_consistency_score=sc)
        assert res.uncomputable and res.confidence is None, (jq, sc)


def test_nonfinite_weights_are_uncomputable():
    res = compute_confidence(jury_quality=0.9, self_consistency_score=0.9, w_q=float("inf"))
    assert res.uncomputable
    res = compute_confidence(jury_quality=0.9, self_consistency_score=0.9, w_q=0.0, w_c=0.0)
    assert res.uncomputable  # zero total weight is not a signal either


def test_judge_vote_rejects_nonfinite_weight():
    import pytest
    from autonomy.decision import JudgeVote
    for bad in (float("inf"), float("nan")):
        with pytest.raises(Exception):
            JudgeVote(judge="x", family="a", voice=1.0, safety=1.0, appr=1.0,
                      reliability_weight=bad)


def test_exploit_chain_weight_inf_now_ends_in_review():
    """E2E regression (panel exploit): weight=inf -> pooled NaN -> min-cap laundered
    NaN to AUTO@1.0. Now: (a) the vote is rejected at construction; (b) even a NaN
    smuggled straight into derive_decision as `confidence` routes REVIEW."""
    import pytest
    from autonomy.decision import JudgeVote, RouteDecision, derive_decision
    with pytest.raises(Exception):
        JudgeVote(judge="x", family="a", voice=1.0, safety=1.0, appr=1.0,
                  reliability_weight=float("inf"))
    votes = [
        JudgeVote(judge="a", family="anthropic", voice=0.95, safety=0.95, appr=0.95),
        JudgeVote(judge="b", family="ollama", voice=0.95, safety=0.95, appr=0.95),
    ]
    from autonomy.aggregate import aggregate_jury
    for bad in (float("nan"), float("inf")):
        decision, esc, _, _ = derive_decision(
            votes=votes, aggregate=aggregate_jury(votes), threshold=0.85, confidence=bad
        )
        assert decision is RouteDecision.REVIEW, bad
        assert "uncomputable" in esc.label
