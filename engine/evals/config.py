"""Gate threshold config for the calibration + accuracy gates (EVAL-03 / rvy.8).

Thresholds live HERE, not hardcoded in the gate logic, because the autonomy dial
will later demand a tighter bar for a higher-autonomy channel (bead edge case) —
a per-channel/per-engine override must not require touching code. ``GateSpec``
carries everything :class:`~kb.schema.EvalMetric` needs to self-judge (threshold +
direction) plus the cadence lane (per-commit vs per-promotion).

Defaults are the spec.md §5 customer-facing bar:
  * ECE ≤ 0.05                      (calibration; LTE; per-commit on recorded conf)
  * precision ≥ 0.95, recall ≥ 0.95 (per classify/extract cell; GTE; per-commit)
  * brand-voice on-voice ≥ 0.90     (GTE; per-promotion — human-rated)
  * Cohen's κ ≥ 0.60                (GTE; per-promotion — label quality gate)

Boundary inclusivity is defined by :meth:`kb.schema.EvalMetric.compute_passed`:
GTE is ``value >= threshold`` and LTE is ``value <= threshold`` — both INCLUSIVE,
so ECE == 0.05 and P/R == 0.95 PASS.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from kb.schema import Direction, RunKind


@dataclass(frozen=True)
class GateSpec:
    """One threshold gate: which metric, the bar, the direction, and the lane."""

    metric: str
    threshold: float
    direction: Direction
    run_kind: RunKind
    # Minimum sample size below which the gate reports not-promotable (never a
    # misleading pass). Stable κ/ECE need more samples than a cheap P/R check.
    min_samples: int = 10
    # Brand-voice's on-voice % gate cannot pass unless label quality (κ) clears
    # this first — names the κ gate it depends on.
    requires_gate: str | None = None


# Canonical metric names (match what the gate writes to eval_metric.metric).
ECE = "ece"
PRECISION = "precision"
RECALL = "recall"
BRAND_VOICE = "brand_voice_on_voice_rate"
KAPPA = "kappa"


@dataclass(frozen=True)
class GateConfig:
    """The full set of gates. Override per (engine, channel) via :meth:`tighten`."""

    specs: tuple[GateSpec, ...]

    def by_metric(self, metric: str) -> GateSpec | None:
        return next((g for g in self.specs if g.metric == metric), None)

    def per_commit(self) -> tuple[GateSpec, ...]:
        return tuple(g for g in self.specs if g.run_kind is RunKind.PER_COMMIT)

    def per_promotion(self) -> tuple[GateSpec, ...]:
        return tuple(g for g in self.specs if g.run_kind is RunKind.PER_PROMOTION)

    def tighten(self, metric: str, threshold: float) -> "GateConfig":
        """Return a copy with one metric's threshold raised (autonomy-dial path)."""
        return GateConfig(
            tuple(replace(g, threshold=threshold) if g.metric == metric else g for g in self.specs)
        )


# The default Phase-2 bar (spec.md §5). Calibration + accuracy are cheap/per-commit;
# the human-rated brand-voice bars are per-promotion (raters can't run every commit).
DEFAULT_GATES = GateConfig(
    specs=(
        # Calibration — ECE plumbing WIRED now, MEASURED Phase 5 (needs AUTON-02
        # per-example confidence); runs on synthetic/recorded confidence until then.
        GateSpec(ECE, 0.05, Direction.LTE, RunKind.PER_COMMIT, min_samples=20),
        # Accuracy — per classify/extract cell, cheap + deterministic per-commit.
        GateSpec(PRECISION, 0.95, Direction.GTE, RunKind.PER_COMMIT, min_samples=10),
        GateSpec(RECALL, 0.95, Direction.GTE, RunKind.PER_COMMIT, min_samples=10),
        # Brand-voice — human-rated, per-promotion. on-voice% needs κ≥0.6 first.
        GateSpec(KAPPA, 0.60, Direction.GTE, RunKind.PER_PROMOTION, min_samples=10),
        GateSpec(
            BRAND_VOICE, 0.90, Direction.GTE, RunKind.PER_PROMOTION,
            min_samples=10, requires_gate=KAPPA,
        ),
    )
)
