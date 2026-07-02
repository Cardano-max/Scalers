"""REAL calibration gate (rvy.8 / D2-as-amended) — DB-free unit tests.

Covers the arch contract's load-bearing properties:
fit-on-CALIBRATION vs measure-on-HOLDOUT (no in-sample tautology), the one-sided
routed-lift bound (underconfidence never penalized; inclusive boundary),
reliability -> NOT_PROMOTABLE (never a pass, never a build-red), SKIP on zero
pairs, structural gating on the calibrated pooled p_est (never routed / never sc
alone), and eval_metric recording (reliable metrics only).
"""

from __future__ import annotations

import itertools

import pytest

from autonomy.confidence import Calibration
from evals.calibration import (
    ConfidencePair,
    collect_confidence_pairs,
    deterministic_probe_confidence_fn,
    fit_on_calibration,
    holdout_ece,
    routed_lift,
    run_calibration_gate,
)
from evals.gate import CellNotBuilt
from evals.metrics import expected_calibration_error
from kb.schema import Direction, Engine, EvalMetric, GoldExample, RunKind, Split

TENANT = "cal-test"
_ids = itertools.count()


# ── helpers ───────────────────────────────────────────────────────────────────


def _example(split: Split, *, correct: bool, p_est: float, routed: float | None = None,
             cell: str = "triage") -> GoldExample:
    """One gold example whose input carries the intended prediction + recorded
    confidences (the injectable confidence_fn reads them back)."""
    i = next(_ids)
    return GoldExample(
        id=f"ex-{i}", tenant_id=TENANT, engine=Engine.ENGAGEMENT, cell=cell,
        input={"pred": "spam", "p_est": p_est, "routed": p_est if routed is None else routed},
        expected={"triage_class": "spam" if correct else "ham"},
        rubric_dimensions=["triage_class"], split=split, label_version=1,
        content_hash=f"h{i}",
    )


def _group(split: Split, n: int, frac_correct: float, p_est: float,
           routed: float | None = None) -> list[GoldExample]:
    n_correct = round(n * frac_correct)
    return [
        _example(split, correct=(i < n_correct), p_est=p_est, routed=routed)
        for i in range(n)
    ]


def _predictor(example: GoldExample) -> dict:
    return {"triage_class": example.input["pred"]}


def _confidence_fn(example: GoldExample, payload: dict):
    return (example.input["p_est"], example.input["routed"])


class FakeStore:
    """Captures record_metric calls; serves gold examples like KbStore.get_gold_set."""

    def __init__(self, examples=()):
        self.examples = list(examples)
        self.metrics: list[EvalMetric] = []

    def get_gold_set(self, *, tenant_id, engine, label_version=None, cell=None, split=None):
        return [
            e for e in self.examples
            if e.tenant_id == tenant_id and e.engine == engine
            and (cell is None or e.cell == cell)
            and (split is None or e.split == split)
        ]

    def record_metric(self, metric: EvalMetric) -> str:
        self.metrics.append(metric)
        return f"metric-{len(self.metrics)}"


def _run(examples, confidence_fn=_confidence_fn, **kw):
    store = FakeStore(examples)
    result = run_calibration_gate(
        store, tenant_id=TENANT, engine=Engine.ENGAGEMENT, cell="triage",
        dimension="triage_class", predictor=_predictor, confidence_fn=confidence_fn, **kw,
    )
    return store, result


def _pair(split: Split, p_est: float, routed: float, correct: bool) -> ConfidencePair:
    return ConfidencePair(
        example_id=f"p-{next(_ids)}", cell="triage", split=split,
        p_est=p_est, routed=routed, correct=correct,
    )


# ── fit-vs-holdout separation (contract §2) ───────────────────────────────────
# (Historical: Calibration.fit/.apply once disagreed on bin-edge membership, so
# these tests dodged exact edges. Fixed at source in autonomy.confidence —
# apply now indexes exactly as fit buckets; regression tests at file bottom
# use exact-edge values on purpose.)


def test_fit_on_calibration_only_and_holdout_detects_miscalibration():
    """In-sample ECE of the fitted remap is ~0 (tautological); the gate measures
    HOLDOUT only, where a differently-distributed holdout still exposes the
    miscalibration."""
    examples = (
        _group(Split.CALIBRATION, 20, 0.50, 0.92)   # overconfident: says 0.92, is 50%
        + _group(Split.CALIBRATION, 20, 0.30, 0.32)
        + _group(Split.HOLDOUT, 20, 0.95, 0.92)     # holdout distributed DIFFERENTLY
        + _group(Split.HOLDOUT, 20, 0.30, 0.32)
    )
    store = FakeStore(examples)
    pairs = collect_confidence_pairs(
        store, _predictor, tenant_id=TENANT, engine=Engine.ENGAGEMENT, cell="triage",
        dimension="triage_class", confidence_fn=_confidence_fn,
    )
    assert len(pairs) == 80

    calibration = fit_on_calibration(pairs)

    # Tautology check: ECE measured on the FIT pairs after remap is ~0 by
    # construction — this is exactly what the gate is forbidden to gate on.
    in_sample = expected_calibration_error(
        [(calibration.apply(p.p_est), p.correct) for p in pairs if p.split is Split.CALIBRATION]
    )
    assert in_sample.value < 0.02

    # The gate's measurement: HOLDOUT ONLY — and it still detects miscalibration.
    hold = holdout_ece(pairs, calibration)
    assert hold.n == 40  # structurally holdout-only (never the 80 total)
    assert hold.reliable
    assert hold.value > 0.05

    # End-to-end: the gate FAILs on the holdout ECE (blocking), not the tautology.
    _, result = _run(examples)
    assert result.verdict == "FAIL"
    assert [o.metric for o in result.failures] == ["calibration_ece_holdout"]


def test_fit_ignores_holdout_pairs():
    # Only CALIBRATION pairs shape the map: a holdout-only set fits to identity.
    pairs = [_pair(Split.HOLDOUT, 0.92, 0.92, True) for _ in range(30)]
    calibration = fit_on_calibration(pairs)
    assert calibration == Calibration()  # identity: nothing was fit


# ── well-calibrated end-to-end -> PASS ────────────────────────────────────────


def test_well_calibrated_end_to_end_pass():
    examples = (
        _group(Split.CALIBRATION, 20, 0.90, 0.92)
        + _group(Split.CALIBRATION, 20, 0.30, 0.32)
        + _group(Split.HOLDOUT, 20, 0.90, 0.92)
        + _group(Split.HOLDOUT, 20, 0.30, 0.32)
    )
    store, result = _run(examples, git_sha="abc123")
    assert result.verdict == "PASS"
    assert {o.metric for o in result.outcomes} == {"calibration_ece_holdout", "routed_lift"}
    assert all(o.passed for o in result.outcomes)

    # eval_metric recording: both reliable metrics recorded, fully attributed.
    by_name = {m.metric: m for m in store.metrics}
    assert set(by_name) == {"calibration_ece_holdout", "routed_lift"}
    ece_row = by_name["calibration_ece_holdout"]
    assert ece_row.direction is Direction.LTE and ece_row.threshold == 0.05
    lift_row = by_name["routed_lift"]
    assert lift_row.direction is Direction.GTE
    assert lift_row.threshold == pytest.approx(0.80)
    for m in store.metrics:
        assert m.passed is True
        assert m.run_kind is RunKind.PER_COMMIT
        assert m.tenant_id == TENANT and m.engine == "ENGAGEMENT" and m.cell == "triage"
        assert m.git_sha == "abc123"


# ── overconfident holdout -> FAIL (blocking) ─────────────────────────────────


def test_overconfident_holdout_fails_and_records_passed_false():
    examples = (
        _group(Split.CALIBRATION, 20, 0.90, 0.92)
        + _group(Split.CALIBRATION, 20, 0.30, 0.32)
        # Holdout drifted: the 0.92 bin is only 50% correct out of sample.
        + _group(Split.HOLDOUT, 20, 0.50, 0.92)
        + _group(Split.HOLDOUT, 20, 0.30, 0.32)
    )
    store, result = _run(examples)
    assert result.verdict == "FAIL"
    ece_out = next(o for o in result.outcomes if o.metric == "calibration_ece_holdout")
    assert ece_out.passed is False and ece_out.value > 0.05
    recorded = next(m for m in store.metrics if m.metric == "calibration_ece_holdout")
    assert recorded.passed is False


# ── lift gate: one-sided, inclusive boundary (contract §3) ────────────────────


def test_routed_below_thr_never_counts_against():
    # 15 fired pairs all correct + 30 UNDER-threshold pairs all wrong: the
    # under-threshold pairs are invisible to the bound (one-sided).
    pairs = (
        [_pair(Split.HOLDOUT, 0.9, 0.9, True) for _ in range(15)]
        + [_pair(Split.HOLDOUT, 0.5, 0.5, False) for _ in range(30)]
    )
    mr = routed_lift(pairs, 0.85)
    assert mr.n == 15          # only routed >= thr counted
    assert mr.value == 1.0     # the 30 wrong-but-unrouted pairs never penalize
    assert mr.reliable


def test_underconfident_but_accurate_is_never_penalized():
    # Perfectly accurate cell whose routed confidence is capped low: nothing
    # would auto-fire, so the lift bound has nothing to judge -> NOT_PROMOTABLE
    # (n=0 < min_n), never a FAIL.
    examples = (
        _group(Split.CALIBRATION, 20, 0.90, 0.92, routed=0.5)
        + _group(Split.CALIBRATION, 20, 0.30, 0.32, routed=0.5)
        + _group(Split.HOLDOUT, 20, 0.90, 0.92, routed=0.5)
        + _group(Split.HOLDOUT, 20, 0.30, 0.32, routed=0.5)
    )
    store, result = _run(examples)
    assert result.verdict == "NOT_PROMOTABLE"
    lift_out = next(o for o in result.outcomes if o.metric == "routed_lift")
    assert lift_out.passed is None and lift_out.n == 0
    assert result.failures == []
    # ECE was reliable + passing and recorded; the unreliable lift recorded NOTHING.
    assert [m.metric for m in store.metrics] == ["calibration_ece_holdout"]


def test_lift_fails_when_conditional_accuracy_below_bound():
    # p_est is honest (ECE passes) but what WOULD auto-fire (routed >= 0.85) is
    # only 70% correct < 0.80 bound -> FAIL on the lift gate alone.
    examples = (
        _group(Split.CALIBRATION, 20, 0.70, 0.72, routed=0.9)
        + _group(Split.CALIBRATION, 20, 0.30, 0.32)
        + _group(Split.HOLDOUT, 20, 0.70, 0.72, routed=0.9)
        + _group(Split.HOLDOUT, 20, 0.30, 0.32)
    )
    _, result = _run(examples)
    assert result.verdict == "FAIL"
    assert [o.metric for o in result.failures] == ["routed_lift"]
    lift_out = result.failures[0]
    assert lift_out.value == pytest.approx(0.70)
    assert lift_out.n == 20  # only the routed>=thr holdout pairs


def test_lift_boundary_exactly_thr_minus_margin_passes_inclusive():
    # 16/20 correct among fired pairs = 0.80 == thr - 0.05 -> GTE inclusive PASS.
    examples = (
        _group(Split.CALIBRATION, 20, 0.80, 0.82, routed=0.9)
        + _group(Split.CALIBRATION, 20, 0.30, 0.32)
        + _group(Split.HOLDOUT, 20, 0.80, 0.82, routed=0.9)
        + _group(Split.HOLDOUT, 20, 0.30, 0.32)
    )
    _, result = _run(examples)
    lift_out = next(o for o in result.outcomes if o.metric == "routed_lift")
    assert lift_out.value == pytest.approx(0.80)
    assert lift_out.passed is True
    assert result.verdict == "PASS"


def test_lift_uses_holdout_pairs_only():
    # CALIBRATION pairs routed above thr must not leak into the lift bound.
    pairs = (
        [_pair(Split.CALIBRATION, 0.9, 0.9, False) for _ in range(20)]  # would tank it
        + [_pair(Split.HOLDOUT, 0.9, 0.9, True) for _ in range(12)]
    )
    mr = routed_lift(pairs, 0.85)
    assert mr.n == 12 and mr.value == 1.0


# ── reliability: min-N / degenerate -> NOT_PROMOTABLE; zero pairs -> SKIP ─────


def test_zero_pairs_is_skip_neutral():
    _, result = _run([])
    assert result.verdict == "SKIP"
    assert result.outcomes == []


def test_confidence_fn_none_skips_pairs_and_gate_skips():
    # The done-gate lane today: examples exist, but no real confidence source ->
    # every pair skipped -> SKIP (never fabricated numbers, never a false red).
    examples = _group(Split.CALIBRATION, 20, 0.9, 0.92) + _group(Split.HOLDOUT, 20, 0.9, 0.92)
    store, result = _run(examples, confidence_fn=lambda ex, payload: None)
    assert result.verdict == "SKIP"
    assert store.metrics == []


def test_insufficient_n_is_not_promotable_not_fail_not_pass():
    examples = (
        _group(Split.CALIBRATION, 3, 1.0, 0.92)
        + _group(Split.HOLDOUT, 3, 1.0, 0.92)
        + _group(Split.HOLDOUT, 3, 0.33, 0.32)
    )
    store, result = _run(examples)
    assert result.verdict == "NOT_PROMOTABLE"
    assert all(o.passed is None for o in result.outcomes)
    assert store.metrics == []  # unreliable metrics record NOTHING


def test_degenerate_confidence_range_is_not_promotable():
    # All p_est identical -> calibrated holdout spread is 0 -> ECE unreliable.
    # The (reliable, passing) lift is still recorded; the verdict is NOT_PROMOTABLE.
    examples = (
        _group(Split.CALIBRATION, 20, 0.90, 0.92)
        + _group(Split.HOLDOUT, 20, 0.90, 0.92)
    )
    store, result = _run(examples)
    assert result.verdict == "NOT_PROMOTABLE"
    ece_out = next(o for o in result.outcomes if o.metric == "calibration_ece_holdout")
    assert ece_out.passed is None and "narrow" in ece_out.reason
    assert [m.metric for m in store.metrics] == ["routed_lift"]


def test_real_fail_wins_over_not_promotable():
    # ECE reliable + failing, lift unreliable (nothing fired): verdict is FAIL.
    examples = (
        _group(Split.CALIBRATION, 20, 0.90, 0.92, routed=0.5)
        + _group(Split.CALIBRATION, 20, 0.30, 0.32, routed=0.5)
        + _group(Split.HOLDOUT, 20, 0.50, 0.92, routed=0.5)   # overconfident holdout
        + _group(Split.HOLDOUT, 20, 0.30, 0.32, routed=0.5)
    )
    _, result = _run(examples)
    assert result.verdict == "FAIL"
    assert [o.metric for o in result.failures] == ["calibration_ece_holdout"]
    lift_out = next(o for o in result.outcomes if o.metric == "routed_lift")
    assert lift_out.passed is None  # unreliable, but the real FAIL wins


# ── structural: the gate input is the calibrated pooled p_est (contract §1) ───


def test_holdout_ece_reads_p_est_never_routed():
    # p_est well-calibrated, routed pure garbage -> ECE stays low.
    good_p_est = (
        [_pair(Split.HOLDOUT, 0.9, 0.01, i < 18) for i in range(20)]
        + [_pair(Split.HOLDOUT, 0.3, 0.99, i < 6) for i in range(20)]
    )
    assert holdout_ece(good_p_est, Calibration()).value <= 0.05

    # p_est miscalibrated, routed perfectly calibrated -> ECE is HIGH anyway:
    # the gate cannot be rescued by routed (it never reads it).
    bad_p_est = (
        [_pair(Split.HOLDOUT, 0.9, 0.5, i < 10) for i in range(20)]
        + [_pair(Split.HOLDOUT, 0.3, 0.3, i < 6) for i in range(20)]
    )
    assert holdout_ece(bad_p_est, Calibration()).value > 0.05


# ── pair collection ───────────────────────────────────────────────────────────


def test_collect_pairs_scores_correctness_on_the_gated_dimension():
    ex_right = _example(Split.HOLDOUT, correct=True, p_est=0.9)    # expected == pred
    ex_wrong = _example(Split.HOLDOUT, correct=False, p_est=0.9)   # expected != pred
    store = FakeStore([ex_right, ex_wrong])
    pairs = collect_confidence_pairs(
        store, _predictor, tenant_id=TENANT, engine=Engine.ENGAGEMENT, cell="triage",
        dimension="triage_class", confidence_fn=_confidence_fn,
    )
    by_id = {p.example_id: p for p in pairs}
    assert by_id[ex_right.id].correct is True
    assert by_id[ex_wrong.id].correct is False


def test_collect_pairs_only_reads_requested_splits():
    smoke = _example(Split.SMOKE, correct=True, p_est=0.9)
    cal = _example(Split.CALIBRATION, correct=True, p_est=0.9)
    store = FakeStore([smoke, cal])
    pairs = collect_confidence_pairs(
        store, _predictor, tenant_id=TENANT, engine=Engine.ENGAGEMENT, cell="triage",
        dimension="triage_class", confidence_fn=_confidence_fn,
    )
    assert [p.example_id for p in pairs] == [cal.id]  # SMOKE never leaks into the gate


def test_unbuilt_cell_yields_skip_not_fail():
    def unbuilt(example):
        raise CellNotBuilt("outreach.prospect_extract cell not built yet")

    examples = _group(Split.CALIBRATION, 20, 0.9, 0.92) + _group(Split.HOLDOUT, 20, 0.9, 0.92)
    store = FakeStore(examples)
    result = run_calibration_gate(
        store, tenant_id=TENANT, engine=Engine.ENGAGEMENT, cell="triage",
        dimension="triage_class", predictor=unbuilt, confidence_fn=_confidence_fn,
    )
    assert result.verdict == "SKIP"
    assert store.metrics == []


# ── the eval-lane confidence source (the shipped done-gate semantics) ─────────


def test_probe_confidence_fn_without_jury_source_returns_none():
    # The done-gate lane TODAY: no real jury quality recorded -> None -> pair
    # skipped -> gate SKIPs. Fabricating a jury quality is forbidden.
    fn = deterministic_probe_confidence_fn(_predictor)
    ex = _example(Split.CALIBRATION, correct=True, p_est=0.9)
    assert fn(ex, _predictor(ex)) is None


def test_probe_confidence_fn_with_real_jury_source_produces_pairs():
    # Deterministic predictor -> K identical probe signatures -> sc = 1.0;
    # pooled with the injected jury quality through the REAL compute_confidence.
    fn = deterministic_probe_confidence_fn(_predictor, jury_quality_source=lambda ex: 0.8)
    ex = _example(Split.CALIBRATION, correct=True, p_est=0.9)
    got = fn(ex, _predictor(ex))
    assert got is not None
    p_est, routed = got
    assert p_est == pytest.approx((0.8 + 1.0) / 2)  # w_q=w_c=0.5, sc=1.0, identity map
    assert routed == p_est  # eval lane applies no cap


def test_probe_confidence_fn_too_few_probes_fails_safe():
    # k below MIN_SAMPLES -> self-consistency None -> uncomputable -> None.
    fn = deterministic_probe_confidence_fn(
        _predictor, k=2, jury_quality_source=lambda ex: 0.8
    )
    ex = _example(Split.CALIBRATION, correct=True, p_est=0.9)
    assert fn(ex, _predictor(ex)) is None


# ── QA regression tests (rvy.8 adversarial-verify findings) ───────────────────


def test_bin_edge_fit_and_apply_agree():
    """HIGH fix: a p_est exactly on a bin edge (0.9) must apply the accuracy of
    the SAME bin fit() bucketed it into (bucket 9), not the bin below. Before the
    fix, apply(0.9) hit empty bucket 8 -> identity -> the fitted correction was
    silently dropped (false red/green at edges)."""
    cal = Calibration.fit([(0.9, i % 2 == 0) for i in range(20)])  # bucket 9 acc=0.5
    assert cal.apply(0.9) == pytest.approx(0.5)
    # End-to-end: edge-valued, consistently distributed CALIBRATION+HOLDOUT must
    # NOT false-red — the remap makes the holdout honest (ECE ~0 -> PASS-side).
    examples = (
        _group(Split.CALIBRATION, 20, 0.50, 0.9, routed=0.5)
        + _group(Split.CALIBRATION, 20, 0.30, 0.3, routed=0.5)
        + _group(Split.HOLDOUT, 20, 0.50, 0.9, routed=0.5)
        + _group(Split.HOLDOUT, 20, 0.30, 0.3, routed=0.5)
    )
    _, result = _run(examples)
    ece_out = next(o for o in result.outcomes if o.metric == "calibration_ece_holdout")
    assert ece_out.reliable and ece_out.passed is True, (
        f"edge-valued p_est must calibrate cleanly, got {ece_out}"
    )


def test_unlabeled_dimension_never_fabricates_correct():
    """MEDIUM fix: an example with no expected label on the gated dimension is
    UNJUDGEABLE -> no pair (never correct=True via None==None inflating lift)."""
    labeled = _group(Split.HOLDOUT, 12, 1.0, 0.92, routed=0.9)
    unlabeled = []
    for i in range(30):
        ex = _example(Split.HOLDOUT, correct=False, p_est=0.92, routed=0.9)
        unlabeled.append(
            GoldExample(
                id=f"unlab-{i}", tenant_id=TENANT, engine=Engine.ENGAGEMENT,
                cell="triage", input=dict(ex.input), expected={},  # no label at all
                rubric_dimensions=[], split=Split.HOLDOUT, label_version=1,
                content_hash=f"u{i}",
            )
        )
    store = FakeStore(labeled + unlabeled)
    pairs = collect_confidence_pairs(
        store, _predictor, tenant_id=TENANT, engine=Engine.ENGAGEMENT, cell="triage",
        dimension="triage_class", confidence_fn=_confidence_fn,
    )
    assert len(pairs) == 12, "unlabeled examples must yield ZERO pairs"
    assert all(p.correct for p in pairs)  # only the genuinely-labeled, correct ones


def test_persisted_lift_threshold_is_exact():
    """MINOR fix: the recorded eval_metric threshold is exactly thr-0.05 (0.80),
    not the float-drift 0.7999999999999999."""
    examples = (
        _group(Split.CALIBRATION, 25, 0.90, 0.92, routed=0.92)
        + _group(Split.HOLDOUT, 25, 0.90, 0.92, routed=0.92)
    )
    store, _ = _run(examples)
    lift_rows = [m for m in store.metrics if m.metric == "routed_lift"]
    assert lift_rows and lift_rows[0].threshold == 0.80
