"""Autonomy decision record (OBS-02) — the persisted shape behind the console
jury card + confidence bar.

A *decision record* is the explainable trace of one action's autonomy outcome:
the per-judge, per-dimension jury scores, the pooled confidence, the per-channel
threshold, the jury agreement, the deterministic gate results, the safety
verdict, the route taken (auto / review / regenerate), and *why* it escalated
(``esc{kind,label}``).

This module owns the **schema** and the **pure derivation** from raw signals to
a record. The full cross-family jury is Phase 5 (AUTON-*); a stub producer
(``autonomy.jury``) fills the jury votes now so the layer + console can be
exercised. When the real jury lands it swaps the vote source — **no schema
change**.

Derivation reuses the canonical pure-code router (``harness.router.route``) for
the confidence/gate/dial decision, then layers the independent safety veto and
jury-quality blockers (split / degraded) on top — matching AUTON-04 (a safety
classifier can veto) and the jury-agreement concept.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from harness.router import DEFAULT_THRESHOLD, route
from harness.state import AutonomyMode, Gate, RouteDecision

if TYPE_CHECKING:  # avoid a runtime import cycle (aggregate imports this module)
    from autonomy.aggregate import JuryAggregate

# A jury whose overall scores span more than this range is "split": jurors
# disagree enough that an auto-fire is not safe even if the pool clears the bar.
AGREEMENT_MIN = 0.5

# Minimum contributing judges before a measured (aggregate) jury may AUTO. Below
# this, agreement is not meaningfully measurable (a lone juror trivially "agrees"),
# so the action routes to review. A single-family or single-seat panel therefore
# cannot auto-fire — cross-family is also enforced at the producer.
MIN_JUDGES_FOR_AGREEMENT = 2


class SafetyVerdict(str, Enum):
    """Independent safety classifier verdict (AUTON-04)."""

    PASS = "pass"
    FLAG = "flag"   # suspicious; escalate but not a hard veto
    VETO = "veto"   # hard block; never auto-fire


class EscKind(str, Enum):
    """Why an action escalated (or did not). ``NONE`` means it may auto-fire."""

    NONE = "none"
    GATE = "gate"                       # a deterministic gate failed -> regenerate
    SAFETY = "safety"                   # safety classifier veto/flag
    SPLIT = "split"                     # jury disagreement (low agreement)
    DEGRADED = "degraded"              # fewer judges responded than expected
    HELD = "held"                       # bead-439 autonomy HOLD; channel not lifted (Phase-5 / 4jx.8)
    BELOW_THRESHOLD = "below_threshold"  # pooled confidence under the auto bar
    MODE = "mode"                       # channel dial forces approve-first


#: The three independently-scored jury dimensions (ADR Decision 1, pmm load-bearing:
#: a post can be in the exact artist voice yet inappropriate — never collapse them).
DIMENSIONS: tuple[str, ...] = ("voice", "safety", "appr")


class JudgeVote(BaseModel):
    """One cross-family judge's per-dimension scores (real jury, AUTON-01 / 4jx.2).

    Each dimension is in ``[0, 1]`` (0–4 rubric anchors normalized). ``family``
    records the model family so the cross-family rule (don't let one family dominate)
    is auditable. The Phase-5 additions over the stub shape are all **defaulted**, so
    existing records/producers keep working:

    * ``on_voice`` — the rubric's brand-voice boolean (distinct from the graded
      ``voice`` score).
    * ``*_hard_fail`` — per-dimension, machine-detectable rubric **disqualifier**
      tags. A hard-fail is a FLOOR, never a low number that weighted averaging can
      wash out (ADR Decision 1): the aggregator reads these SEPARATELY, before the
      mean. Per-dimension because a hard appropriateness/safety fail must sink the
      action even at voice≈1.
    * ``reliability_weight`` — this judge's aggregation weight (default uniform;
      gold-calibrated when available). A judge dropped for timeout/error is given a
      reduced weight, never silently counted as agreement.
    """

    model_config = {"frozen": True}

    judge: str
    family: str | None = None
    voice: float = Field(ge=0.0, le=1.0)
    safety: float = Field(ge=0.0, le=1.0)
    appr: float = Field(ge=0.0, le=1.0)
    on_voice: bool = True
    voice_hard_fail: bool = False
    safety_hard_fail: bool = False
    appr_hard_fail: bool = False
    reliability_weight: float = Field(default=1.0, ge=0.0)

    @property
    def overall(self) -> float:
        """This juror's mean across the three dimensions."""
        return (self.voice + self.safety + self.appr) / 3.0

    def score_for(self, dimension: str) -> float:
        """This juror's ``[0,1]`` score on one dimension (``voice``/``safety``/``appr``)."""
        return getattr(self, dimension)

    def hard_fail_for(self, dimension: str) -> bool:
        """Whether this juror tagged a hard-fail disqualifier on ``dimension``."""
        return getattr(self, f"{dimension}_hard_fail")

    def hard_fail_dims(self) -> frozenset[str]:
        """The set of dimensions this juror hard-failed (a disqualifier on any)."""
        return frozenset(d for d in DIMENSIONS if self.hard_fail_for(d))

    @property
    def hard_fail(self) -> bool:
        """Per-judge disqualifier flag = a hard-fail on ANY dimension. This is the
        queryable signal persisted to ``autonomy_jury.hard_fail`` (4jx.10); WHICH
        dimension is recorded on the decision's ``esc_label``."""
        return bool(self.hard_fail_dims())


class GateResult(BaseModel):
    """A deterministic gate result in the console's ``{label, ok}`` shape."""

    model_config = {"frozen": True}

    label: str
    ok: bool

    @classmethod
    def from_gate(cls, gate: Gate) -> "GateResult":
        return cls(label=gate.name, ok=gate.passed)


class Escalation(BaseModel):
    """Why the action escalated — drives the console's escalation chip."""

    model_config = {"frozen": True}

    kind: EscKind
    label: str


class DecisionRecord(BaseModel):
    """The full, persisted autonomy decision for one action (the jury-card model)."""

    model_config = {"frozen": True}

    decision_id: str
    run_id: str
    tenant_id: str
    channel: str
    action_kind: str

    jury: list[JudgeVote]
    pooled_confidence: float
    threshold: float
    agreement: float
    # The generation-stability half of confidence (self-consistency, 4jx.3); None
    # until that bead computes it. Persisted to autonomy_decisions.self_consistency
    # (4jx.10) so the console/eval can show both confidence inputs.
    self_consistency: float | None = None
    gates: list[GateResult] = Field(default_factory=list)
    safety_verdict: SafetyVerdict = SafetyVerdict.PASS
    decision: RouteDecision
    esc: Escalation
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# --------------------------------------------------------------------------- #
# Pure derivation: raw signals -> (pooled confidence, agreement, decision, esc)
# --------------------------------------------------------------------------- #


def pool_confidence(votes: list[JudgeVote]) -> float:
    """Pool the jury into one confidence: the mean of jurors' overall scores.

    (Phase 5 may calibrate / weight by family; the field shape is unchanged.)
    """
    if not votes:
        return 0.0
    return sum(v.overall for v in votes) / len(votes)


def agreement(votes: list[JudgeVote]) -> float:
    """Jury agreement in ``[0, 1]``: ``1 - (max overall - min overall)``.

    Unanimous jurors -> ``1.0``; maximally split -> ``0.0``. A single juror
    trivially "agrees" (``1.0``) — degraded coverage is captured separately.
    """
    if len(votes) < 2:
        return 1.0
    overalls = [v.overall for v in votes]
    return max(0.0, 1.0 - (max(overalls) - min(overalls)))


def derive_decision(
    *,
    votes: list[JudgeVote],
    threshold: float = DEFAULT_THRESHOLD,
    gates: list[Gate] | None = None,
    autonomy: AutonomyMode = AutonomyMode.AUTO,
    safety_verdict: SafetyVerdict = SafetyVerdict.PASS,
    expected_judges: int | None = None,
    aggregate: "JuryAggregate | None" = None,
    catalog_drift: bool = False,
    catalog_drift_reason: str = "",
    confidence: float | None = None,
    confidence_uncomputable: bool = False,
) -> tuple[RouteDecision, Escalation, float, float]:
    """Derive ``(decision, escalation, pooled_confidence, agreement)`` from signals.

    Order of precedence (first match wins) for the escalation reason:

    1. a deterministic gate failed       -> ``regenerate`` (esc=gate)
    2. safety veto/flag                   -> ``review`` (esc=safety)
    3. rubric **hard-fail** on any dim    -> ``review`` (esc=gate, hard-fail tag)
    4. jury split (agreement < min)       -> ``review`` (esc=split)
    5. degraded (missing judges)          -> ``review`` (esc=degraded)
    6. pooled confidence < threshold      -> ``review`` (esc=below_threshold)
    7. channel dial is REVIEW             -> ``review`` (esc=mode)
    8. otherwise                          -> ``auto`` (esc=none)

    Steps 2–5 are auto-blockers the pure router does not know about; they force
    review even when confidence clears the bar.

    When the real ``aggregate`` (ADR Decision 1) is supplied, ``pooled`` and
    ``agree`` come from its reliability-weighted per-dimension signals (``pooled``,
    ``worst_agreement``) and the **hard-fail floor** (#3) fires on any per-dimension
    disqualifier — checked SEPARATELY, never averaged into the mean. Without it, the
    legacy overall-based pooling is used (the stub path), unchanged.
    """
    gates = gates or []
    if aggregate is not None:
        pooled = aggregate.pooled
        agree = aggregate.worst_agreement
    else:
        pooled = pool_confidence(votes)
        agree = agreement(votes)
    # The COMPUTED confidence (4jx.3) — jury_quality pooled with self-consistency,
    # calibrated — is the router's confidence when supplied (replaces the hardcoded
    # 0.9 / jury-only pooling on the real decision path).
    if confidence is not None:
        pooled = confidence

    # Base decision from the canonical pure-code router (gates + confidence + dial).
    base = route(pooled, threshold, gates, autonomy)

    if base is RouteDecision.REGENERATE:
        return base, Escalation(kind=EscKind.GATE, label="deterministic gate failed"), pooled, agree

    if safety_verdict is not SafetyVerdict.PASS:
        return (
            RouteDecision.REVIEW,
            Escalation(kind=EscKind.SAFETY, label=f"safety {safety_verdict.value}"),
            pooled,
            agree,
        )

    # FAIL-SAFE on rubric catalog drift (#81): a judge emitted an unknown hard-fail
    # code or scored against a different catalog_version — the floor can't be trusted,
    # so escalate to a human rather than risk a silent pass. Sits with the floors,
    # after safety.
    if catalog_drift:
        return (
            RouteDecision.REVIEW,
            Escalation(kind=EscKind.GATE, label=f"jury catalog drift ({catalog_drift_reason})"),
            pooled,
            agree,
        )

    # FAIL-SAFE on uncomputable confidence (4jx.3): too few probe samples to estimate
    # self-consistency -> review. "Couldn't compute" is never treated as high confidence.
    if confidence_uncomputable:
        return (
            RouteDecision.REVIEW,
            Escalation(kind=EscKind.BELOW_THRESHOLD, label="confidence uncomputable (insufficient samples)"),
            pooled,
            agree,
        )

    # The hard-fail FLOOR (ADR Decision 1/4): a tagged rubric disqualifier on ANY
    # dimension can never be averaged out by a high score elsewhere — it forces
    # review regardless of the pooled confidence. Checked before split/threshold.
    # Backstop: read the floor from the aggregate when present, ELSE straight from
    # the votes' own per-dimension flags — so a caller that passes real votes WITHOUT
    # building an aggregate can never auto-fire hard-failed content.
    if aggregate is not None:
        hard_fail_dims: frozenset[str] = aggregate.hard_fail_dims
    else:
        hard_fail_dims = frozenset().union(*(v.hard_fail_dims() for v in votes)) if votes else frozenset()
    if hard_fail_dims:
        dims = ", ".join(sorted(hard_fail_dims))
        return (
            RouteDecision.REVIEW,
            Escalation(kind=EscKind.GATE, label=f"rubric hard-fail ({dims})"),
            pooled,
            agree,
        )

    # Insufficient panel (aggregate path): fewer than 2 contributing judges means
    # no cross-judge agreement can be measured — never AUTO on a lone juror, even if
    # a custom panel set expected_judges=1. A measured panel needs >= 2 voices.
    if aggregate is not None and aggregate.n_judges < MIN_JUDGES_FOR_AGREEMENT:
        return (
            RouteDecision.REVIEW,
            Escalation(
                kind=EscKind.DEGRADED,
                label=f"insufficient jury ({aggregate.n_judges} < {MIN_JUDGES_FOR_AGREEMENT} judges)",
            ),
            pooled,
            agree,
        )

    if agree < AGREEMENT_MIN:
        return (
            RouteDecision.REVIEW,
            Escalation(kind=EscKind.SPLIT, label=f"jury split (agreement {agree:.2f})"),
            pooled,
            agree,
        )

    if expected_judges is not None and len(votes) < expected_judges:
        return (
            RouteDecision.REVIEW,
            Escalation(
                kind=EscKind.DEGRADED,
                label=f"degraded jury ({len(votes)}/{expected_judges} judges)",
            ),
            pooled,
            agree,
        )

    if pooled < threshold:
        return (
            RouteDecision.REVIEW,
            Escalation(kind=EscKind.BELOW_THRESHOLD, label=f"confidence {pooled:.2f} < {threshold:.2f}"),
            pooled,
            agree,
        )

    if base is RouteDecision.REVIEW:  # autonomy dial forced approve-first
        return base, Escalation(kind=EscKind.MODE, label="channel set to approve-first"), pooled, agree

    return base, Escalation(kind=EscKind.NONE, label="auto"), pooled, agree
