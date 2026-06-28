"""Calibration + accuracy GATES (EVAL-03 / rvy.8).

Turns the metric math (:mod:`evals.metrics`) into pass/fail gates wired to the
:class:`~kb.schema.EvalMetric` store — the authoritative gating source of truth
(ADR rvy.1). Each gate:

  compute metric → build an EvalMetric (threshold+direction) → record it →
  classify the outcome as PASS / FAIL / SKIPPED / NOT_PROMOTABLE.

Outcome semantics (bead edge cases):
  * **PASS** — enough data, value clears the bar (inclusive boundary).
  * **FAIL** — enough data, value misses the bar. The only status that FAILS a
    per-commit CI build.
  * **SKIPPED** — no gold data for that cell/metric yet (engines 2/3 land later):
    neutral, never a false build failure.
  * **NOT_PROMOTABLE** — data exists but is insufficient/unreliable (small N,
    narrow ECE range, or a dependency gate like κ<0.6 failed): not a misleading
    pass, but also not a per-commit build break — it blocks PROMOTION (the 439
    autonomy gate) instead.

``GateReport.enforce_per_commit()`` is the CI build-fail hook; ``promotable()``
is what the Phase-5 autonomy gate (439) consults.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from evals.config import BRAND_VOICE, DEFAULT_GATES, ECE, KAPPA, PRECISION, RECALL, GateConfig, GateSpec
from evals.metrics import (
    MetricResult,
    classification_prf,
    cohens_kappa,
    expected_calibration_error,
    extraction_prf,
    on_voice_rate,
)
from kb.schema import EvalMetric, RunKind, Scope


class GateStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"            # no data — neutral
    NOT_PROMOTABLE = "not_promotable"  # data too thin / dependency failed


@dataclass(frozen=True)
class GateOutcome:
    metric: str
    status: GateStatus
    value: float | None
    threshold: float
    cell: str | None
    run_kind: RunKind
    n: int
    reason: str | None = None
    eval_metric_id: str | None = None

    @property
    def blocks_build(self) -> bool:
        """Only a hard FAIL on a per-commit gate breaks the build."""
        return self.status is GateStatus.FAIL and self.run_kind is RunKind.PER_COMMIT


class GateFailed(RuntimeError):
    """Raised by enforce_per_commit() when a per-commit gate hard-fails."""


@dataclass
class GateReport:
    outcomes: list[GateOutcome] = field(default_factory=list)

    def add(self, o: GateOutcome) -> None:
        self.outcomes.append(o)

    def failures(self) -> list[GateOutcome]:
        return [o for o in self.outcomes if o.blocks_build]

    def enforce_per_commit(self) -> None:
        """CI build-fail hook: raise if any per-commit gate hard-failed.

        SKIPPED and NOT_PROMOTABLE never break the build (neutral / promotion-
        blocking only) — exactly one missed bar with real data does.
        """
        fails = self.failures()
        if fails:
            lines = "; ".join(f"{o.metric}{f'[{o.cell}]' if o.cell else ''}={o.value} vs {o.threshold}" for o in fails)
            raise GateFailed(f"{len(fails)} gate(s) failed: {lines}")

    def promotable(self) -> bool:
        """True only if every evaluated gate PASSED — what the 439 autonomy gate
        consults. Any FAIL / SKIPPED / NOT_PROMOTABLE blocks promotion."""
        return bool(self.outcomes) and all(o.status is GateStatus.PASS for o in self.outcomes)

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for o in self.outcomes:
            out[o.status.value] = out.get(o.status.value, 0) + 1
        return out


# --------------------------------------------------------------------------- #
# Single-gate judging + recording
# --------------------------------------------------------------------------- #


def _judge(spec: GateSpec, mr: MetricResult, *, dependency_ok: bool = True) -> tuple[GateStatus, str | None]:
    if mr.n == 0:
        return GateStatus.SKIPPED, "no gold data"
    if not mr.reliable or mr.n < spec.min_samples:
        return GateStatus.NOT_PROMOTABLE, mr.detail.get("reason", f"insufficient sample (n={mr.n})")
    if not dependency_ok:
        return GateStatus.NOT_PROMOTABLE, f"dependency gate {spec.requires_gate} not passed"
    em = EvalMetric(metric=spec.metric, value=mr.value, threshold=spec.threshold, direction=spec.direction)
    return (GateStatus.PASS if em.compute_passed() else GateStatus.FAIL), None


def record_and_judge(
    spec: GateSpec,
    mr: MetricResult,
    *,
    store: Any | None = None,
    tenant_id: str | None = None,
    engine: str | None = None,
    cell: str | None = None,
    dependency_ok: bool = True,
    **metric_ctx: Any,
) -> GateOutcome:
    """Judge a metric against its gate and (if data exists) record it to eval_metric.

    SKIPPED gates are NOT recorded (nothing measured). PASS/FAIL/NOT_PROMOTABLE
    write a row so trends + the promotion decision have history.
    """
    status, reason = _judge(spec, mr, dependency_ok=dependency_ok)
    metric_id = None
    if status is not GateStatus.SKIPPED and store is not None:
        em = EvalMetric(
            metric=spec.metric, value=mr.value,
            scope=Scope.TENANT if tenant_id else Scope.GLOBAL,
            tenant_id=tenant_id, engine=engine, cell=cell,
            threshold=spec.threshold, direction=spec.direction, run_kind=spec.run_kind,
            passed=(status is GateStatus.PASS),
            **metric_ctx,
        )
        metric_id = store.record_metric(em)
    return GateOutcome(
        metric=spec.metric, status=status, value=(mr.value if mr.n else None),
        threshold=spec.threshold, cell=cell, run_kind=spec.run_kind, n=mr.n,
        reason=reason, eval_metric_id=metric_id,
    )


# --------------------------------------------------------------------------- #
# Cell-level gate bundles
# --------------------------------------------------------------------------- #


def accuracy_gates(
    pairs: list[tuple],
    *,
    cell: str,
    kind: str = "classification",
    config: GateConfig = DEFAULT_GATES,
    store: Any | None = None,
    tenant_id: str | None = None,
    engine: str | None = None,
    **ctx: Any,
) -> list[GateOutcome]:
    """Precision + recall gates for one classify/extract cell.

    Computes the cell's P/R/F1 once, then judges the PRECISION and RECALL gates
    independently (each must clear ≥ 0.95). ``kind`` selects field-level vs label
    P/R. An empty ``pairs`` skips both gates (neutral).
    """
    mr = extraction_prf(pairs) if kind == "extraction" else classification_prf(pairs)
    p_spec, r_spec = config.by_metric(PRECISION), config.by_metric(RECALL)
    outs: list[GateOutcome] = []
    for spec, key in ((p_spec, "precision"), (r_spec, "recall")):
        if spec is None:
            continue
        sub = MetricResult(spec.metric, mr.detail.get(key, 0.0), mr.n, reliable=mr.reliable, detail=mr.detail)
        outs.append(record_and_judge(spec, sub, store=store, tenant_id=tenant_id, engine=engine, cell=cell, **ctx))
    return outs


def calibration_gate(
    conf_pairs: list[tuple[float, bool]],
    *,
    config: GateConfig = DEFAULT_GATES,
    store: Any | None = None,
    tenant_id: str | None = None,
    engine: str | None = None,
    cell: str | None = None,
    **ctx: Any,
) -> GateOutcome:
    """ECE calibration gate. WIRED now / MEASURED Phase 5 — runs on synthetic or
    recorded self-consistency confidence until AUTON-02 emits per-example scores."""
    spec = config.by_metric(ECE)
    assert spec is not None
    mr = expected_calibration_error(conf_pairs, min_samples=spec.min_samples)
    return record_and_judge(spec, mr, store=store, tenant_id=tenant_id, engine=engine, cell=cell, **ctx)


def brand_voice_gates(
    rater_pairs: list[tuple],
    consensus_labels: list[bool],
    *,
    config: GateConfig = DEFAULT_GATES,
    store: Any | None = None,
    tenant_id: str | None = None,
    engine: str | None = None,
    cell: str | None = None,
    **ctx: Any,
) -> list[GateOutcome]:
    """Brand-voice promotion gates: Cohen's κ (label quality) then on-voice %.

    The on-voice ≥ 90% gate is NOT_PROMOTABLE unless κ ≥ 0.6 passes first — bad
    label agreement means the % is untrustworthy (the gate fails on label quality
    first, per the bead).
    """
    k_spec, v_spec = config.by_metric(KAPPA), config.by_metric(BRAND_VOICE)
    outs: list[GateOutcome] = []

    kappa_pass = True
    if k_spec is not None:
        k_mr = cohens_kappa(rater_pairs, min_samples=k_spec.min_samples)
        k_out = record_and_judge(k_spec, k_mr, store=store, tenant_id=tenant_id, engine=engine, cell=cell, **ctx)
        outs.append(k_out)
        kappa_pass = k_out.status is GateStatus.PASS

    if v_spec is not None:
        v_mr = on_voice_rate(consensus_labels, min_samples=v_spec.min_samples)
        outs.append(record_and_judge(
            v_spec, v_mr, store=store, tenant_id=tenant_id, engine=engine, cell=cell,
            dependency_ok=kappa_pass, **ctx,
        ))
    return outs
