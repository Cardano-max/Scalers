"""The per-commit eval gate (rvy.9) — threshold registry -> eval_metric -> verdict.

This is the Phase-2 integration proof's engine: it reads a gold set, runs a
``Predictor`` (the cell-under-test) over it with ``live=False`` (deterministic, no
model keys), scores each registered metric, writes an ``eval_metric`` row, and
returns a PASS / FAIL / SKIP verdict — FAIL with a message naming the metric and
the gold-set version. "eval-on-every-change" is real only once this has been
*observed* failing on a seeded regression and passing on a clean change.

Wiring seam for rvy.7 / rvy.8 (scaffold now, swap when they land):
* ``Predictor`` is where rvy.7's Inspect mock-model solver plugs in (per-commit
  ``live=False``); ``dataset_for`` is the Inspect dataset adapter (ADR Decision 3).
* The metric ``kind`` computations call ``evals.scoring`` — rvy.8 may replace them
  with DeepEval-backed scorers behind the same registry, no gate-code change.
* ``GATES`` is the threshold registry (ADR Decision 4's ``thresholds.yaml``,
  expressed as typed data) — .7/.8 add rows here without touching the runner.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from evals import scoring
from kb.schema import Direction, Engine, EvalMetric, GoldExample, RunKind, Split

# A predictor is the cell-under-test: gold example in, predicted label payload out
# (same shape as ``expected``). Pure/deterministic for the per-commit path.
Predictor = Callable[[GoldExample], dict[str, Any]]


@dataclass(frozen=True)
class GateThreshold:
    """One registered gate (a row of ADR Decision 4's threshold registry)."""

    metric: str
    engine: Engine
    cell: str
    kind: str            # macro_recall | class_recall | exact_accuracy | accuracy | ece
    threshold: float
    direction: Direction
    run_kind: RunKind
    required: bool = True
    dimension: str = ""  # which expected key this metric reads
    target: str = ""     # for class_recall: the class whose recall is gated


# ── Threshold registry (ADR Decision 4) ──────────────────────────────────────
# Per-commit (hermetic, offline): classify/extract P-R + calibration ECE.
# Per-promotion (human/jury, live): brand-voice — recorded here as scaffold but
# NEVER gates a per-commit run (the honest cadence).
GATES: list[GateThreshold] = [
    GateThreshold("triage_recall", Engine.ENGAGEMENT, "triage", "macro_recall",
                  0.95, Direction.GTE, RunKind.PER_COMMIT, dimension="triage_class"),
    GateThreshold("safety_recall_must_escalate", Engine.ENGAGEMENT, "triage", "class_recall",
                  0.95, Direction.GTE, RunKind.PER_COMMIT, dimension="reply_safety", target="must-escalate"),
    GateThreshold("extraction_accuracy", Engine.OUTREACH, "prospect_extract", "exact_accuracy",
                  0.95, Direction.GTE, RunKind.PER_COMMIT, dimension="extraction"),
    # Calibration ECE (<=0.05) is rvy.8's gate — it owns the per-example confidence
    # source (synthetic in Phase 2). It registers here behind the same contract;
    # the `ece` scorer is provided in evals.scoring. Left out of this proof's
    # registry so the deterministic classify/extract verdict stays independent of
    # the confidence-calibration question.
    # Per-promotion scaffold — recorded, NOT part of the per-commit verdict.
    GateThreshold("brand_voice_onvoice", Engine.POSTING, "copywriter", "accuracy",
                  0.90, Direction.GTE, RunKind.PER_PROMOTION, dimension="on_voice"),
]


@dataclass(frozen=True)
class MetricOutcome:
    metric: str
    engine: str
    cell: str
    value: float
    threshold: float
    direction: Direction
    passed: bool
    required: bool
    run_kind: RunKind
    n: int
    gold_set_version: str  # "label_version=<v> dataset=<hash8>"


@dataclass
class GateResult:
    verdict: str                                   # PASS | FAIL | SKIP
    outcomes: list[MetricOutcome] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def failures(self) -> list[MetricOutcome]:
        return [o for o in self.outcomes if o.required and not o.passed]

    def message(self) -> str:
        if self.verdict == "PASS":
            return f"eval gate PASS - {len(self.outcomes)} metric(s) within threshold"
        if self.verdict == "SKIP":
            return f"eval gate SKIP - no gold data ({', '.join(self.skipped)})"
        lines = ["eval gate FAIL - required metric(s) regressed:"]
        for o in self.failures:
            arrow = ">=" if o.direction is Direction.GTE else "<="
            lines.append(
                f"  - {o.engine}.{o.cell}.{o.metric} = {o.value:.3f} (needs {arrow} {o.threshold})"
                f" [gold {o.gold_set_version}, n={o.n}]"
            )
        return "\n".join(lines)


def dataset_for(store, engine: Engine, cell: str, tenant_id: str, split: Split = Split.SMOKE) -> list[GoldExample]:
    """Inspect dataset adapter (ADR Decision 3): gold rows for one (engine, cell)."""
    return [e for e in store.get_gold_set(tenant_id=tenant_id, engine=engine, split=split) if e.cell == cell]


def _dataset_hash(rows: list[GoldExample]) -> str:
    payload = json.dumps(
        sorted(f"{r.content_hash}:{json.dumps(r.expected, sort_keys=True)}" for r in rows)
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:8]


def _value_for(g: GateThreshold, rows: list[GoldExample], preds: list[dict[str, Any]]) -> float:
    if g.kind == "exact_accuracy":
        pred = [p.get(g.dimension) for p in preds]
        exp = [r.expected.get(g.dimension) for r in rows]
        return scoring.accuracy(pred, exp)
    if g.kind == "accuracy":
        pred = [p.get(g.dimension) for p in preds]
        exp = [r.expected.get(g.dimension) for r in rows]
        return scoring.accuracy(pred, exp)
    if g.kind == "macro_recall":
        pred = [p.get(g.dimension) for p in preds]
        exp = [r.expected.get(g.dimension) for r in rows]
        return scoring.macro_recall(pred, exp)
    if g.kind == "class_recall":
        pred = [p.get(g.dimension) for p in preds]
        exp = [r.expected.get(g.dimension) for r in rows]
        return scoring.recall_of_class(pred, exp, g.target)
    if g.kind == "ece":
        conf, correct = [], []
        for r, p in zip(rows, preds, strict=True):
            c = r.input.get("recorded_confidence")
            if c is None:
                continue
            conf.append(float(c))
            correct.append(p.get(g.dimension) == r.expected.get(g.dimension))
        return scoring.expected_calibration_error(conf, correct)
    raise ValueError(f"unknown metric kind {g.kind!r}")


def _passes(value: float, threshold: float, direction: Direction) -> bool:
    return value >= threshold if direction is Direction.GTE else value <= threshold


def run_eval_gate(
    store,
    predictor: Predictor,
    *,
    tenant_id: str,
    run_kind: RunKind = RunKind.PER_COMMIT,
    git_sha: str | None = None,
    record: bool = True,
) -> GateResult:
    """Run every registered gate for ``run_kind``, record metrics, return a verdict.

    A gate over an empty gold set is SKIP (neutral), never a silent pass that masks
    a real failure elsewhere. The verdict is FAIL iff a *required* gate's metric
    misses its threshold — proving the gate fires via the metric, not a crash.
    """
    result = GateResult(verdict="PASS")
    any_scored = False

    for g in GATES:
        if g.run_kind is not run_kind:
            continue
        rows = dataset_for(store, g.engine, g.cell, tenant_id)
        if not rows:
            result.skipped.append(f"{g.engine.value}.{g.cell}.{g.metric}")
            continue
        any_scored = True
        preds = [predictor(r) for r in rows]
        value = _value_for(g, rows, preds)
        passed = _passes(value, g.threshold, g.direction)
        version = f"label_version={rows[0].label_version} dataset={_dataset_hash(rows)}"

        result.outcomes.append(MetricOutcome(
            metric=g.metric, engine=g.engine.value, cell=g.cell, value=value,
            threshold=g.threshold, direction=g.direction, passed=passed,
            required=g.required, run_kind=g.run_kind, n=len(rows), gold_set_version=version,
        ))
        if record:
            store.record_metric(EvalMetric(
                metric=g.metric, value=value, tenant_id=tenant_id,
                engine=g.engine.value, cell=g.cell, threshold=g.threshold,
                direction=g.direction, passed=passed, run_kind=g.run_kind,
                label_version=rows[0].label_version, dataset_hash=_dataset_hash(rows),
                git_sha=git_sha,
            ))

    if not any_scored:
        result.verdict = "SKIP"
    elif result.failures:
        result.verdict = "FAIL"
    return result
