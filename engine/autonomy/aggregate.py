"""Deterministic jury aggregation (AUTON-01 / 4jx.2, ADR Phase-5 Decision 1).

Pure code — no model calls, fully reproducible. Turns a panel of per-judge,
per-dimension :class:`~autonomy.decision.JudgeVote`s into the pooled signals the
autonomy decision consumes, with the two properties the ADR makes load-bearing:

* **Hard-fail is a FLOOR, computed SEPARATELY from the mean.** ``hard_fail[d] =
  any(judge.hard_fail on d)`` is evaluated *before* and *outside* the weighted
  average, so a hard appropriateness/safety disqualifier can **never** be washed
  out by a high voice score. (The mean is reliability-weighted; the floor is not a
  number in that mean at all.)
* **Agreement is REAL, per dimension, from the judges' actual divergence** —
  ``agreement[d] = 1 − spread(score_j[d])`` — never the stub's hardcoded 1.0. The
  decision uses the **worst** dimension's agreement: a split on *any* dimension is a
  split.

Dimensions (``voice``/``safety``/``appr``) are scored **independently** and never
collapsed into one judgment.
"""

from __future__ import annotations

from dataclasses import dataclass

from autonomy.decision import DIMENSIONS, JudgeVote


@dataclass(frozen=True)
class JuryAggregate:
    """The pooled, per-dimension result of aggregating a jury panel."""

    dim_score: dict[str, float]      # reliability-weighted mean per dimension, [0,1]
    hard_fail: dict[str, bool]       # any-judge hard-fail per dimension (the floor)
    agreement: dict[str, float]      # 1 - spread per dimension, [0,1]
    n_judges: int                    # judges that actually contributed a vote
    total_weight: float              # Σ reliability_weight over contributing judges

    @property
    def any_hard_fail(self) -> bool:
        """True if ANY dimension was hard-failed by ANY judge — the disqualifier."""
        return any(self.hard_fail.values())

    @property
    def hard_fail_dims(self) -> frozenset[str]:
        return frozenset(d for d, hf in self.hard_fail.items() if hf)

    @property
    def pooled(self) -> float:
        """One pooled quality scalar = the mean of the per-dimension scores.

        Each ``dim_score`` is already reliability-weighted across judges; pooling
        across the (independent) dimensions gives the single ``jury_quality`` the
        confidence pooler (4jx.3) and the router consume. A hard-fail is NOT folded
        in here — it is a separate floor the decision layer reads."""
        if not self.dim_score:
            return 0.0
        return sum(self.dim_score.values()) / len(self.dim_score)

    @property
    def worst_agreement(self) -> float:
        """The minimum agreement across dimensions — a split on any dimension is a
        split. ``0.0`` when there are no judges (no signal ⇒ never trustworthy)."""
        if not self.agreement:
            return 0.0
        return min(self.agreement.values())


def _weighted_mean(pairs: list[tuple[float, float]]) -> float:
    """Σ(w·x)/Σw over (weight, value) pairs; 0.0 if total weight is 0 (no signal)."""
    total_w = sum(w for w, _ in pairs)
    if total_w <= 0.0:
        return 0.0
    return sum(w * x for w, x in pairs) / total_w


def _spread_agreement(scores: list[float]) -> float:
    """``1 - (max - min)`` over a dimension's scores.

    A single judge trivially "agrees" (1.0) — under-coverage is caught separately by
    the degraded check, not by faking disagreement. Zero judges ⇒ 0.0 (no signal).
    """
    if not scores:
        return 0.0
    if len(scores) < 2:
        return 1.0
    return max(0.0, 1.0 - (max(scores) - min(scores)))


def aggregate_jury(votes: list[JudgeVote]) -> JuryAggregate:
    """Aggregate a jury panel into per-dimension pooled signals (ADR Decision 1).

    ``votes`` is the panel that ACTUALLY responded — a judge dropped for
    timeout/error is simply absent here (its absence reduces coverage; it is never
    counted as agreement). Reliability weights ride on each :class:`JudgeVote`.
    """
    dim_score: dict[str, float] = {}
    hard_fail: dict[str, bool] = {}
    agreement: dict[str, float] = {}

    for d in DIMENSIONS:
        # hard_fail computed SEPARATELY, before/outside the mean — the floor.
        hard_fail[d] = any(v.hard_fail_for(d) for v in votes)
        # reliability-weighted mean of the dimension's [0,1] scores.
        dim_score[d] = _weighted_mean([(v.reliability_weight, v.score_for(d)) for v in votes])
        # real agreement from the judges' actual divergence on this dimension.
        agreement[d] = _spread_agreement([v.score_for(d) for v in votes])

    return JuryAggregate(
        dim_score=dim_score,
        hard_fail=hard_fail,
        agreement=agreement,
        n_judges=len(votes),
        total_weight=sum(v.reliability_weight for v in votes),
    )
