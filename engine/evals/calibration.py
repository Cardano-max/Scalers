"""REAL calibration gate (rvy.8, Phase-5 D2-as-amended / PR #93) — fit-vs-holdout
ECE + one-sided routed-lift bound over REAL recorded confidences.

The arch contract (D2-as-amended, panel-reviewed):

1. **Gate input = (p_est, correct) pairs** where ``p_est`` is the CALIBRATED POOLED
   estimate from :mod:`autonomy.confidence` (``compute_confidence`` /
   pool-confidence path). The gate NEVER judges the routed/capped confidence
   symmetrically and NEVER the self-consistency component alone — both are
   observability-only signals.
2. **Fit on CALIBRATION, measure on HOLDOUT.** The calibration map is fitted ONLY
   on ``split=CALIBRATION`` gold pairs; ECE is measured ONLY on ``split=HOLDOUT``
   pairs. In-sample ECE of a histogram remap is ~0 by construction — gating on the
   fit pairs would be tautological and is forbidden (see the fit-vs-holdout test).
3. **Companion LIFT gate (directional routed bound):**
   ``P(correct | routed >= thr) >= thr - 0.05`` per cell, ONE-SIDED — cap-induced
   UNDERconfidence is never penalized. Pairs routed below ``thr`` never count
   against the bound; only the conditional accuracy of what WOULD auto-fire is
   bounded.
4. **Reliability is honored:** insufficient data (min-N) or a degenerate
   confidence range yields NOT_PROMOTABLE — never a pass, never a build-red
   (:class:`evals.metrics.MetricResult`.``reliable`` models this).
5. **Thresholds (inclusive):** holdout ECE <= 0.05 (LTE), lift >= thr - 0.05
   (GTE). BLOCKING: a FAIL on real recorded pairs reds the build (exit 1 via
   ``evals.run_gate``). SKIP-neutral when no confidence pairs are recorded yet.

HONESTY SCOPE (read before trusting a green): the harness and the blocking wiring
are real NOW; the *numbers* become meaningful only as real probe/jury data flows
through the eval lane. Under the deterministic per-commit lane the self-consistency
of a pure predictor is near-degenerate (K identical probe samples -> sc = 1.0), and
no real jury quality is recorded for gold examples yet, so the done-gate lane SKIPs
today rather than fabricating confidence (see
:func:`deterministic_probe_confidence_fn`). A green calibration gate therefore does
NOT mean "the engine is calibrated" until real jury/probe confidences flow — it
means the gate machinery is live and will red the build the moment real pairs miss
the bar. Cite: ADR Phase-5 Decision 2 as amended (PR #93). ``p_est`` is also
persisted per decision in ``autonomy_decisions.confidence_components`` (D6,
4jx.17) — :func:`pairs_from_decisions` is the offline-recompute reader for it;
this eval-lane harness additionally computes ``p_est`` per gold example via the
real :mod:`autonomy.confidence` pipeline with an injectable deterministic probe
sampler.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from autonomy.confidence import (
    DEFAULT_K,
    IDENTITY_CALIBRATION,
    Calibration,
    compute_confidence,
    self_consistency,
)
from evals.gate import CellNotBuilt, Predictor
from evals.metrics import MetricResult, expected_calibration_error
from kb.schema import Direction, Engine, EvalMetric, GoldExample, RunKind, Split

# Gate constants (D2-as-amended). Both boundaries are INCLUSIVE.
ECE_THRESHOLD = 0.05      # holdout ECE must be <= this (LTE)
LIFT_MARGIN = 0.05        # lift bound = thr - LIFT_MARGIN (GTE)
DEFAULT_THR = 0.85        # default auto-fire threshold the lift gate is anchored to
DEFAULT_MIN_N = 10        # min routed>=thr pairs before the lift value is trusted

ECE_METRIC = "calibration_ece_holdout"
LIFT_METRIC = "routed_lift"
# ECE measured symmetrically on the ROUTED/capped value — OBSERVABILITY ONLY
# (4jx.16 AC5): cap-induced underconfidence makes it read high by design, so it
# is recorded with no threshold and never enters any verdict. Gating on it is
# explicitly rejected (arch D2-as-amended).
ECE_ROUTED_OBS_METRIC = "ece_routed_observability"

# (example, predictor payload) -> (p_est, routed) | None.
# p_est  = calibrated pooled estimate (THE gate input, contract §1).
# routed = post-cap routed value (lift gate + observability ONLY, never gated
#          symmetrically). None = confidence not computable for this example ->
#          the pair is skipped (fail-safe: never fabricate confidence).
ConfidenceFn = Callable[[GoldExample, dict[str, Any]], "tuple[float, float] | None"]


@dataclass(frozen=True)
class ConfidencePair:
    """One gold example's recorded confidence vs. ground truth.

    ``p_est`` is the CALIBRATED POOLED estimate from the real
    :func:`autonomy.confidence.compute_confidence` path — the calibration gate's
    input (contract §1). ``routed`` is the post-cap routed value, consumed ONLY by
    the one-sided lift gate and observability; it is never gated symmetrically.
    """

    example_id: str
    cell: str
    split: Split
    p_est: float
    routed: float
    correct: bool


def pairs_from_decisions(
    items: "Sequence[tuple[Any, bool, Split]]", *, cell: str
) -> list[ConfidencePair]:
    """The rvy.8 OFFLINE-RECOMPUTE reader (4jx.17 AC1): rebuild ``ConfidencePair``s
    from persisted decision records + ground-truth labels.

    ``p_est`` comes from the decision's ``confidence_components["p_est"]`` — the
    calibrated pooled gate input — because ``pooled_confidence`` stores the CAPPED
    routed value, from which ``p_est`` is unrecoverable. ``routed`` is that capped
    ``pooled_confidence``. A decision with no components (stub path, uncomputable
    confidence) contributes NO pair — never fabricate a gate input.

    ``items`` is ``(record, correct, split)`` per labeled decision; ``record`` is
    duck-typed (needs ``decision_id``, ``pooled_confidence``,
    ``confidence_components``) to keep the eval lane import-light."""
    pairs: list[ConfidencePair] = []
    for record, correct, split in items:
        comps = record.confidence_components
        if not comps or "p_est" not in comps:
            continue
        pairs.append(ConfidencePair(
            example_id=record.decision_id, cell=cell, split=split,
            p_est=float(comps["p_est"]), routed=float(record.pooled_confidence),
            correct=bool(correct),
        ))
    return pairs


def collect_confidence_pairs(
    store,
    predictor: Predictor,
    *,
    tenant_id: str,
    engine: Engine,
    cell: str,
    dimension: str,
    confidence_fn: ConfidenceFn,
    splits: Sequence[Split] = (Split.CALIBRATION, Split.HOLDOUT),
) -> list[ConfidencePair]:
    """Run the real cell + real confidence pipeline over the gold set -> pairs.

    For each gold example in each split: ``predictor(example) -> payload``;
    ``correct = payload[dimension] == expected[dimension]`` (the gated dimension);
    ``(p_est, routed) = confidence_fn(example, payload)``. A ``None`` from
    ``confidence_fn`` skips the pair (fail-safe — no real confidence, no pair,
    never a fabricated number). An unbuilt cell (:class:`CellNotBuilt`) yields no
    pairs, so the gate SKIPs (never a false fail).
    """
    pairs: list[ConfidencePair] = []
    for split in splits:
        examples = store.get_gold_set(tenant_id=tenant_id, engine=engine, cell=cell, split=split)
        for ex in examples:
            try:
                payload = predictor(ex)
            except CellNotBuilt:
                return pairs  # unbuilt cell -> nothing scoreable -> SKIP-neutral upstream
            est = confidence_fn(ex, payload)
            if est is None:
                continue  # fail-safe: uncomputable confidence -> skip, never fabricate
            p_est, routed = est
            expected_label = (ex.expected or {}).get(dimension)
            if expected_label is None:
                # Unlabeled on the gated dimension: correctness is UNJUDGEABLE.
                # Skip the pair — a None==None match must never fabricate
                # correct=True and inflate the lift/ECE (rvy.8 QA finding).
                continue
            correct = payload.get(dimension) == expected_label
            pairs.append(
                ConfidencePair(
                    example_id=ex.id, cell=ex.cell, split=ex.split,
                    p_est=float(p_est), routed=float(routed), correct=bool(correct),
                )
            )
    return pairs


def fit_on_calibration(pairs: Sequence[ConfidencePair], *, n_bins: int = 10) -> Calibration:
    """Fit the calibration map ONLY on ``split=CALIBRATION`` pairs (contract §2).

    Uses the real :meth:`autonomy.confidence.Calibration.fit` over the pairs'
    ``(p_est, correct)``. HOLDOUT pairs never influence the map — they are what
    the map is judged against.
    """
    fit_pairs = [(p.p_est, p.correct) for p in pairs if p.split is Split.CALIBRATION]
    return Calibration.fit(fit_pairs, n_bins=n_bins)


def holdout_ece(pairs: Sequence[ConfidencePair], calibration: Calibration) -> MetricResult:
    """ECE of the calibrated ``p_est`` over HOLDOUT pairs ONLY (contract §2).

    Applies ``calibration.apply`` to each HOLDOUT pair's ``p_est`` and measures
    :func:`evals.metrics.expected_calibration_error` over ``(calibrated_p,
    correct)``. Measuring on the fit (CALIBRATION) pairs is tautological (a
    histogram remap has ~0 in-sample ECE by construction) and is exactly what
    this function does NOT do. Structurally reads ``p_est`` — never ``routed``,
    never the self-consistency component alone (contract §1).
    """
    hold = [
        (calibration.apply(p.p_est), p.correct) for p in pairs if p.split is Split.HOLDOUT
    ]
    return expected_calibration_error(hold)


def routed_lift(
    pairs: Sequence[ConfidencePair], thr: float, *, min_n: int = DEFAULT_MIN_N
) -> MetricResult:
    """One-sided routed bound over HOLDOUT pairs: ``P(correct | routed >= thr)``.

    ONE-SIDED (contract §3): pairs with ``routed < thr`` are simply not counted —
    cap-induced underconfidence is never penalized. Only the conditional accuracy
    of what WOULD auto-fire is bounded: value = fraction correct among
    ``routed >= thr`` pairs, gated ``>= thr - 0.05`` (GTE, inclusive). ``n`` is
    the routed>=thr count; ``reliable`` requires ``n >= min_n`` (default 10) —
    an unreliable lift is NOT_PROMOTABLE, never a fail and never a pass.
    HOLDOUT only, for consistency with the ECE gate.
    """
    hold = [p for p in pairs if p.split is Split.HOLDOUT]
    fired = [p for p in hold if p.routed >= thr]
    n = len(fired)
    value = (sum(1 for p in fired if p.correct) / n) if n else 0.0
    detail: dict[str, Any] = {
        "thr": thr, "bound": thr - LIFT_MARGIN, "n_routed": n, "n_holdout": len(hold),
    }
    if n < min_n:
        detail["reason"] = f"insufficient routed>=thr sample (n={n}<{min_n})"
    return MetricResult(LIFT_METRIC, value, n, reliable=n >= min_n, detail=detail)


@dataclass(frozen=True)
class CalibrationOutcome:
    """One judged calibration-gate metric. ``passed is None`` == not judged
    (unreliable data -> NOT_PROMOTABLE; nothing recorded to eval_metric)."""

    metric: str
    value: float
    threshold: float
    direction: Direction
    n: int
    reliable: bool
    passed: bool | None
    reason: str = ""


@dataclass
class CalibrationGateResult:
    """PASS | FAIL | SKIP | NOT_PROMOTABLE + per-metric outcomes.

    Semantics: no pairs at all -> SKIP (neutral). Any reliable metric missing its
    bar -> FAIL (a real FAIL always wins). Otherwise any unreliable metric ->
    NOT_PROMOTABLE (blocks promotion, not the build). Else PASS.
    """

    verdict: str
    outcomes: list[CalibrationOutcome] = field(default_factory=list)
    n_pairs: int = 0

    @property
    def failures(self) -> list[CalibrationOutcome]:
        return [o for o in self.outcomes if o.passed is False]

    def message(self) -> str:
        if self.verdict == "SKIP":
            return (
                "calibration gate SKIP - no confidence pairs recorded yet "
                "(real jury/probe confidence not flowing in the eval lane; "
                "blocking flips live the moment pairs flow)"
            )
        heads = {
            "PASS": (
                f"calibration gate PASS - {len(self.outcomes)} metric(s) within threshold "
                f"over {self.n_pairs} real recorded pair(s)"
            ),
            "NOT_PROMOTABLE": (
                "calibration gate NOT_PROMOTABLE - confidence data insufficient/degenerate "
                "(blocks promotion, not the build):"
            ),
            "FAIL": "calibration gate FAIL - real recorded confidence missed the bar:",
        }
        lines = [heads[self.verdict]]
        for o in self.outcomes:
            arrow = ">=" if o.direction is Direction.GTE else "<="
            status = "PASS" if o.passed else ("FAIL" if o.passed is False else "NOT_PROMOTABLE")
            line = f"  - {o.metric} = {o.value:.3f} (needs {arrow} {o.threshold:.3f}) n={o.n} [{status}]"
            if o.reason:
                line += f" ({o.reason})"
            lines.append(line)
        return "\n".join(lines)


def run_calibration_gate(
    store,
    *,
    tenant_id: str,
    engine: Engine,
    cell: str,
    dimension: str,
    predictor: Predictor,
    confidence_fn: ConfidenceFn,
    thr: float = DEFAULT_THR,
    ece_threshold: float = ECE_THRESHOLD,
    min_n: int = DEFAULT_MIN_N,
    record: bool = True,
    git_sha: str | None = None,
    confidence_provenance: str | None = None,
) -> CalibrationGateResult:
    """Run the D2-as-amended calibration gate for one (engine, cell, dimension).

    Pipeline: collect real (p_est, routed, correct) pairs over the CALIBRATION +
    HOLDOUT gold splits -> fit the calibration map on CALIBRATION only -> judge
    holdout ECE (LTE ``ece_threshold``) + one-sided routed lift (GTE
    ``thr - 0.05``) on HOLDOUT only.

    Recording (``record=True``): a RELIABLE metric writes one ``eval_metric`` row
    (metric name, value, threshold, direction, ``passed`` set explicitly,
    run_kind=PER_COMMIT, engine/cell/tenant set). An UNRELIABLE metric records
    NOTHING — the not-promotable reason is carried on the result only, so the
    authoritative metric history never contains numbers too thin to trust.
    """
    pairs = collect_confidence_pairs(
        store, predictor, tenant_id=tenant_id, engine=engine, cell=cell,
        dimension=dimension, confidence_fn=confidence_fn,
    )
    if not pairs:
        return CalibrationGateResult(verdict="SKIP")
    return _judge_and_record(
        pairs, thr=thr, ece_threshold=ece_threshold, min_n=min_n,
        record=record, metric_store=store, tenant_id=tenant_id,
        engine=engine, cell=cell, channel=None, git_sha=git_sha,
        confidence_provenance=confidence_provenance,
    )


def run_decision_calibration_gate(
    labeled: "Sequence[tuple[Any, bool, Split]]",
    *,
    store: Any | None = None,
    tenant_id: str,
    engine: Engine | str,
    channel: str,
    thr: float = DEFAULT_THR,
    ece_threshold: float = ECE_THRESHOLD,
    min_n: int = DEFAULT_MIN_N,
    record: bool = True,
    git_sha: str | None = None,
) -> CalibrationGateResult:
    """The DECISIONS-sourced two-gate runner (4jx.16), per channel.

    ``labeled`` is ``(decision_record, correct, split)`` from the route-independent
    gold audit (labeling protocol §8). ``p_est`` is read from each decision's
    ``confidence_components`` — NEVER from ``pooled_confidence`` (the capped routed
    value); decisions without components (stub path / uncomputable) contribute no
    pair. Fit on CALIBRATION-split labels only; holdout ECE + the one-sided
    directional bound ``P(correct | routed >= thr) >= thr - 0.05`` measured on
    HOLDOUT only. Rows are recorded per (tenant, engine, channel) with the
    decisions' confidence provenance — the 4jx.8 lift-precondition grain."""
    pairs = pairs_from_decisions(labeled, cell=channel)
    if not pairs:
        return CalibrationGateResult(verdict="SKIP")
    provs = {
        rec.confidence_provenance
        for rec, _, _ in labeled
        if getattr(rec, "confidence_components", None)
    }
    provenance = next(iter(provs)) if len(provs) == 1 else ("mixed" if provs else None)
    return _judge_and_record(
        pairs, thr=thr, ece_threshold=ece_threshold, min_n=min_n,
        record=record, metric_store=store, tenant_id=tenant_id,
        engine=engine, cell=None, channel=channel, git_sha=git_sha,
        confidence_provenance=provenance,
    )


def _judge_and_record(
    pairs: Sequence[ConfidencePair],
    *,
    thr: float,
    ece_threshold: float,
    min_n: int,
    record: bool,
    metric_store: Any | None,
    tenant_id: str,
    engine: Engine | str,
    cell: str | None,
    channel: str | None,
    git_sha: str | None,
    confidence_provenance: str | None,
) -> CalibrationGateResult:
    """Shared judge+record core for both lanes (gold-example and decisions).

    Fits on CALIBRATION pairs, judges holdout ECE (LTE) + the one-sided routed
    lift (GTE), records reliable metrics to eval_metric, and emits the
    ECE-on-routed OBSERVABILITY row (no threshold, ``passed=None``) — which never
    participates in the verdict."""
    calibration = fit_on_calibration(pairs)
    ece_mr = holdout_ece(pairs, calibration)
    lift_mr = routed_lift(pairs, thr, min_n=min_n)

    result = CalibrationGateResult(verdict="PASS", n_pairs=len(pairs))
    engine_v = engine.value if isinstance(engine, Engine) else str(engine)

    judged: list[tuple[MetricResult, str, float, Direction]] = [
        (ece_mr, ECE_METRIC, ece_threshold, Direction.LTE),
        # round() kills float drift (0.85-0.05 -> 0.7999999...) so the persisted
        # eval_metric threshold is the exact bound downstream consumers compare.
        (lift_mr, LIFT_METRIC, round(thr - LIFT_MARGIN, 6), Direction.GTE),
    ]
    for mr, name, threshold, direction in judged:
        if not mr.reliable:
            result.outcomes.append(CalibrationOutcome(
                metric=name, value=mr.value, threshold=threshold, direction=direction,
                n=mr.n, reliable=False, passed=None,
                reason=str(mr.detail.get("reason", "unreliable")),
            ))
            continue  # unreliable -> NOT recorded (authoritative history stays clean)
        passed = mr.value >= threshold if direction is Direction.GTE else mr.value <= threshold
        result.outcomes.append(CalibrationOutcome(
            metric=name, value=mr.value, threshold=threshold, direction=direction,
            n=mr.n, reliable=True, passed=passed,
        ))
        if record and metric_store is not None:
            metric_store.record_metric(EvalMetric(
                metric=name, value=mr.value, tenant_id=tenant_id, engine=engine_v,
                cell=cell, channel=channel, threshold=threshold, direction=direction,
                passed=passed, run_kind=RunKind.PER_COMMIT, git_sha=git_sha,
                # 4jx.17 AC2: which producer computed the gate's confidence input
                # (the LiftController's per-channel lift-precondition-(e) signal).
                confidence_provenance=confidence_provenance,
            ))

    # 4jx.16 AC5: ECE on the ROUTED/capped value, symmetric — recorded for
    # observability (no threshold, passed=None) and NEVER judged: cap-induced
    # underconfidence makes it read high by design. Decisions lane only
    # (channel set): in the gold-example lane routed == p_est by construction
    # (no cap is applied there), so the row would only duplicate ECE_METRIC.
    routed_hold = [(p.routed, p.correct) for p in pairs if p.split is Split.HOLDOUT]
    if record and metric_store is not None and channel is not None and routed_hold:
        obs_mr = expected_calibration_error(routed_hold)
        metric_store.record_metric(EvalMetric(
            metric=ECE_ROUTED_OBS_METRIC, value=obs_mr.value, tenant_id=tenant_id,
            engine=engine_v, cell=cell, channel=channel, threshold=None,
            direction=None, passed=None, run_kind=RunKind.PER_COMMIT,
            git_sha=git_sha, confidence_provenance=confidence_provenance,
        ))

    if result.failures:
        result.verdict = "FAIL"  # a real FAIL always wins over NOT_PROMOTABLE
    elif any(o.passed is None for o in result.outcomes):
        result.verdict = "NOT_PROMOTABLE"
    return result


def lift_preconditions_ab(
    store: Any,
    *,
    tenant_id: str,
    engine: Engine | str,
    channel: str,
) -> tuple[bool, list[str]]:
    """D5 lift preconditions (a)+(b) for one (tenant, engine, channel) — the
    4jx.8 LiftController consumer surface.

    (a) holdout ECE gate and (b) the directional routed bound hold iff the
    LATEST recorded row of each metric at this grain exists and passed. A
    missing row blocks: never lift on the absence of proof (an unmeasured
    channel is exactly the case the gates exist for). Returns
    ``(ok, blocking_reasons)``."""
    engine_v = engine.value if isinstance(engine, Engine) else str(engine)
    reasons: list[str] = []
    for metric in (ECE_METRIC, LIFT_METRIC):
        rows = store.get_metrics(
            tenant_id=tenant_id, engine=engine_v, channel=channel, metric=metric,
        )
        if not rows:
            reasons.append(
                f"{metric}: no recorded row for channel {channel!r} "
                "(never lift on absence of proof)"
            )
        elif rows[-1].passed is not True:  # rows are created_at-ordered
            reasons.append(
                f"{metric}: latest row not passing "
                f"(value={rows[-1].value}, threshold={rows[-1].threshold})"
            )
    return (not reasons, reasons)


# ── Eval-lane confidence source (the done-gate wiring, rvy.8) ─────────────────


def payload_signature(payload: dict[str, Any]) -> str:
    """Stable JSON signature of a predictor payload (the probe-sample reduction)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def deterministic_probe_confidence_fn(
    predictor: Predictor,
    *,
    k: int = DEFAULT_K,
    jury_quality_source: Callable[[GoldExample], "float | None"] | None = None,
    calibration: Calibration = IDENTITY_CALIBRATION,
) -> ConfidenceFn:
    """The eval lane's REAL confidence source: the actual 4jx.3 pipeline with a
    deterministic probe sampler.

    Per example: sample the (deterministic) predictor K times as the probe,
    reduce each sample to a stable JSON signature, score
    :func:`autonomy.confidence.self_consistency`, then pool with the REAL jury
    quality via :func:`autonomy.confidence.compute_confidence` — the same code
    path production uses (contract §1).

    ``jury_quality_source`` is the honesty seam. ``compute_confidence`` REQUIRES
    a jury quality; fabricating one (a constant 0.9 etc.) is FORBIDDEN. Real jury
    quality is the 4jx.2 :class:`autonomy.aggregate.JuryAggregate.pooled` — it is
    not recorded for gold examples in the eval lane yet, so the default source is
    ``None`` -> every pair is skipped -> the done-gate calibration verdict is
    SKIP today. That is correct and intentional: the gate never fabricates
    confidence, and blocking flips live the moment a real jury-quality source is
    wired in (no code change here — inject the source).

    Returns ``(p_est, routed)`` where ``p_est`` is the calibrated pooled estimate
    and ``routed`` equals ``p_est`` in this lane (the eval lane applies no
    channel cap; in production ``routed`` is the post-cap value). ``None`` when
    confidence is uncomputable (no jury quality, or too few probe samples).
    """

    def fn(example: GoldExample, payload: dict[str, Any]) -> tuple[float, float] | None:
        jq = jury_quality_source(example) if jury_quality_source is not None else None
        if jq is None:
            return None  # no REAL jury quality -> no pair (never fabricate confidence)
        signatures = [payload_signature(predictor(example)) for _ in range(k)]
        sc = self_consistency(signatures)
        conf = compute_confidence(
            jury_quality=jq, self_consistency_score=sc, calibration=calibration
        )
        if conf.confidence is None:
            return None  # uncomputable -> fail safe (never high confidence from noise)
        p_est = conf.confidence
        routed = p_est  # eval lane: no channel cap applied -> routed == pooled estimate
        return (p_est, routed)

    return fn
