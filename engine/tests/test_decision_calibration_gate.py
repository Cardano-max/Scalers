"""4jx.16 — two-gate confidence honesty on the DECISIONS lane (DB-free).

The decisions-sourced runner reads ``p_est`` from
``autonomy_decisions.confidence_components`` (never ``pooled_confidence``, which
stores the capped ROUTED value), fits the calibration map on CALIBRATION-split
labels only, measures holdout ECE + the per-channel one-sided directional bound
P(correct | routed >= thr) >= thr - 0.05, and emits eval_metric rows (incl. the
ECE-on-routed OBSERVABILITY row, which never gates).

The load-bearing panel constructions live here:
  * ECE-on-p_est GREEN while the directional gate RED (a cap-surviving
    overconfident subgroup is invisible to ECE — the reason the second gate exists);
  * an underconfident-only miscalibration passes (one-sided; a symmetric routed
    ECE is explicitly observability-only).
"""

from __future__ import annotations

import itertools

import pytest

from autonomy.decision import DecisionRecord, Escalation, EscKind
from evals.calibration import (
    ECE_METRIC,
    ECE_ROUTED_OBS_METRIC,
    LIFT_METRIC,
    lift_preconditions_ab,
    run_decision_calibration_gate,
)
from harness.state import RouteDecision
from kb.schema import Engine, EvalMetric, Split

TENANT = "dcal-test"
CHANNEL = "instagram"
_ids = itertools.count()


class FakeMetricStore:
    """Captures record_metric calls; serves get_metrics like KbStore."""

    def __init__(self) -> None:
        self.metrics: list[EvalMetric] = []

    def record_metric(self, metric: EvalMetric) -> str:
        self.metrics.append(metric)
        return f"m{len(self.metrics)}"

    def get_metrics(self, *, tenant_id, engine=None, cell=None, metric=None,
                    channel=None, **_):
        return [
            m for m in self.metrics
            if m.tenant_id == tenant_id
            and (engine is None or m.engine == engine)
            and (metric is None or m.metric == metric)
            and (channel is None or m.channel == channel)
        ]


def _decision(p_est: float, routed: float, *, channel: str = CHANNEL,
              components: bool = True,
              provenance: str = "computed_min_cap_v1") -> DecisionRecord:
    i = next(_ids)
    return DecisionRecord(
        decision_id=f"d{i}", run_id="r", tenant_id=TENANT, channel=channel,
        action_kind="post", jury=[], pooled_confidence=routed, threshold=0.85,
        agreement=1.0,
        confidence_components=(
            {"raw": p_est, "p_est": p_est, "jury_quality": routed,
             "self_consistency": 1.0, "cap_bind_delta": max(0.0, p_est - routed)}
            if components else None
        ),
        confidence_provenance=provenance,
        decision=RouteDecision.AUTO,
        esc=Escalation(kind=EscKind.NONE, label="auto"),
    )


def _labeled_group(split: Split, n: int, frac_correct: float, p_est: float,
                   routed: float, **kw):
    n_correct = round(n * frac_correct)
    return [(_decision(p_est, routed, **kw), i < n_correct, split) for i in range(n)]


def _both_splits(n: int, frac_correct: float, p_est: float, routed: float, **kw):
    return (_labeled_group(Split.CALIBRATION, n, frac_correct, p_est, routed, **kw)
            + _labeled_group(Split.HOLDOUT, n, frac_correct, p_est, routed, **kw))


def _run(labeled, **kw):
    store = FakeMetricStore()
    result = run_decision_calibration_gate(
        labeled, store=store, tenant_id=TENANT, engine=Engine.POSTING,
        channel=CHANNEL, **kw,
    )
    return store, result


# Shared fixtures: L = calibrated low bin (gives ECE its confidence spread);
# G = cap-underconfident but accurate (routes BELOW thr — invisible to the lift
# gate); B'/B = the cap-surviving routed>=thr subgroup, honest resp. rotten.
def _passing_world():
    return (
        _both_splits(40, 0.30, 0.30, 0.30)     # L: calibrated low bin
        + _both_splits(90, 87 / 90, 0.95, 0.50)  # G: underconfident cap, accurate
        + _both_splits(20, 0.95, 0.95, 0.86)     # B': routed>=thr subgroup, honest
    )


# ── the panel construction (why the second gate exists) ──────────────────────


def test_overconfident_cap_surviving_subgroup_fails_directional_gate_while_ece_green():
    """AC2 (panel BLOCKER construction): the p_est=0.95 bin is calibrated ON
    AVERAGE (ECE ~0), but the only items that actually route AUTO (routed>=thr)
    are a small cap-surviving subgroup with true accuracy 0.70 — ECE-on-p_est
    structurally cannot see it; the directional gate is the one that reds."""
    labeled = (
        _both_splits(40, 0.30, 0.30, 0.30)       # L: spread for a meaningful ECE
        + _both_splits(90, 87 / 90, 0.95, 0.50)  # G: accurate mass, routes BELOW thr
        + _both_splits(10, 0.70, 0.95, 0.86)     # B: rotten subgroup, routes AUTO
    )
    store, result = _run(labeled)
    assert result.verdict == "FAIL"
    by_metric = {o.metric: o for o in result.outcomes}
    assert by_metric[ECE_METRIC].passed is True          # ECE is blind here...
    lift = by_metric[LIFT_METRIC]
    assert lift.passed is False                          # ...the directional gate is not
    assert lift.value == pytest.approx(0.70)
    assert lift.threshold == pytest.approx(0.80)
    assert lift.n == 10  # exactly the cap-surviving routed>=thr holdout subgroup


def test_underconfident_only_miscalibration_passes_one_sided():
    # AC2 second half: cap-induced UNDERconfidence is never penalized — G routes
    # far below its accuracy and B' clears the bound honestly -> both gates green.
    store, result = _run(_passing_world())
    assert result.verdict == "PASS"
    assert all(o.passed for o in result.outcomes if o.metric in (ECE_METRIC, LIFT_METRIC))


# ── AC1: p_est source + fit-pair exclusion on the decisions lane ─────────────


def test_gate_reads_p_est_from_components_never_pooled_confidence():
    """Decisions whose capped pooled_confidence is wildly LOWER than the honest
    p_est: gating on components passes; gating on pooled_confidence would red
    the ECE (structural source check, not a numerology accident)."""
    store, result = _run(_passing_world())
    assert result.verdict == "PASS"
    # G's pooled/routed value is 0.50 with accuracy 0.967 — an ECE fed from
    # pooled_confidence would read |0.97-0.5|-ish over most of the mass. Prove
    # what WAS measured is the components' p_est by the recorded green ECE row:
    ece_rows = [m for m in store.metrics if m.metric == ECE_METRIC]
    assert len(ece_rows) == 1 and ece_rows[0].passed is True
    assert ece_rows[0].value <= 0.05


def test_calibration_split_labels_never_enter_the_measured_gates():
    """AC1: fit pairs are EXCLUDED from both measured gates. A poison group that
    exists only in the CALIBRATION split (routed 0.95, all incorrect — it would
    crash the directional gate if leaked) changes neither verdict nor the gates'
    measured n."""
    poison = _labeled_group(Split.CALIBRATION, 30, 0.0, 0.05, 0.95)
    store, result = _run(_passing_world() + poison)
    assert result.verdict == "PASS"
    by_metric = {o.metric: o for o in result.outcomes}
    assert by_metric[LIFT_METRIC].n == 20          # B' holdout only — no poison
    assert by_metric[ECE_METRIC].n == 150          # holdout pairs only (40+90+20)


def test_decisions_without_components_contribute_no_pair():
    # Stub-path / uncomputable decisions (components=None) are silently excluded
    # — never fabricate a gate input from the capped routed value.
    stubs = [( _decision(0.95, 0.95, components=False, provenance="stub_jury_v0"),
               False, Split.HOLDOUT)] * 25
    store, result = _run(_passing_world() + stubs)
    assert result.verdict == "PASS"
    by_metric = {o.metric: o for o in result.outcomes}
    assert by_metric[ECE_METRIC].n == 150 and by_metric[LIFT_METRIC].n == 20


# ── AC5: ECE-on-routed is observability-only ─────────────────────────────────


def test_ece_on_routed_recorded_as_observability_never_gates():
    """The routed values are massively miscalibrated downward (G: routed 0.50 vs
    accuracy 0.967) so a symmetric routed-ECE reads huge — yet the verdict is
    PASS and the row is recorded with no threshold/passed (observability only)."""
    store, result = _run(_passing_world())
    assert result.verdict == "PASS"
    obs = [m for m in store.metrics if m.metric == ECE_ROUTED_OBS_METRIC]
    assert len(obs) == 1
    assert obs[0].value > 0.15                     # the symmetric read IS terrible
    assert obs[0].passed is None and obs[0].threshold is None  # ...and never gates
    assert all(o.metric != ECE_ROUTED_OBS_METRIC for o in result.outcomes)


# ── AC4: reliability -> not-promotable ───────────────────────────────────────


def test_insufficient_routed_n_is_not_promotable():
    labeled = (
        _both_splits(40, 0.30, 0.30, 0.30)
        + _both_splits(90, 87 / 90, 0.95, 0.50)
        + _both_splits(4, 1.0, 0.95, 0.86)   # only 4 routed>=thr -> below min_n
    )
    store, result = _run(labeled)
    assert result.verdict == "NOT_PROMOTABLE"
    lift = next(o for o in result.outcomes if o.metric == LIFT_METRIC)
    assert lift.passed is None and lift.reliable is False
    # an unreliable metric is never recorded (authoritative history stays clean)
    assert all(m.metric != LIFT_METRIC for m in store.metrics)


def test_zero_labeled_decisions_is_skip_neutral():
    store, result = _run([])
    assert result.verdict == "SKIP"
    assert store.metrics == []


# ── AC2/AC3: per-channel rows + the 4jx.8 lift-precondition surface ──────────


def test_rows_carry_channel_and_provenance():
    store, result = _run(_passing_world())
    assert store.metrics
    for m in store.metrics:
        assert m.channel == CHANNEL
        assert m.confidence_provenance == "computed_min_cap_v1"
        assert m.tenant_id == TENANT and m.engine == "POSTING"


def test_lift_preconditions_ab_require_both_latest_rows_green():
    """AC3: the 4jx.8 consumer surface — preconditions (a) ECE + (b) directional
    bound hold iff the LATEST row of each metric for (tenant, engine, channel)
    exists and passed. Missing or failing rows block with a stated reason."""
    store, _ = _run(_passing_world())
    ok, reasons = lift_preconditions_ab(
        store, tenant_id=TENANT, engine=Engine.POSTING, channel=CHANNEL)
    assert ok and reasons == []

    # A later FAILING lift row supersedes the green one -> blocked.
    store.record_metric(EvalMetric(
        metric=LIFT_METRIC, value=0.70, tenant_id=TENANT, engine="POSTING",
        channel=CHANNEL, threshold=0.80, passed=False,
    ))
    ok, reasons = lift_preconditions_ab(
        store, tenant_id=TENANT, engine=Engine.POSTING, channel=CHANNEL)
    assert not ok and any(LIFT_METRIC in r for r in reasons)

    # A channel with no rows at all is blocked (never lift on absence of proof).
    ok, reasons = lift_preconditions_ab(
        store, tenant_id=TENANT, engine=Engine.POSTING, channel="gmail")
    assert not ok and len(reasons) == 2
