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

from pydantic import BaseModel, Field

from harness.router import DEFAULT_THRESHOLD, route
from harness.state import AutonomyMode, Gate, RouteDecision

# A jury whose overall scores span more than this range is "split": jurors
# disagree enough that an auto-fire is not safe even if the pool clears the bar.
AGREEMENT_MIN = 0.5


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


class JudgeVote(BaseModel):
    """One cross-family judge's per-dimension scores (placeholder until Phase 5).

    Each dimension is in ``[0, 1]``. ``family`` records the model family so the
    cross-family rule (don't let one family dominate) is auditable.
    """

    model_config = {"frozen": True}

    judge: str
    family: str | None = None
    voice: float = Field(ge=0.0, le=1.0)
    safety: float = Field(ge=0.0, le=1.0)
    appr: float = Field(ge=0.0, le=1.0)

    @property
    def overall(self) -> float:
        """This juror's mean across the three dimensions."""
        return (self.voice + self.safety + self.appr) / 3.0


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
) -> tuple[RouteDecision, Escalation, float, float]:
    """Derive ``(decision, escalation, pooled_confidence, agreement)`` from signals.

    Order of precedence (first match wins) for the escalation reason:

    1. a deterministic gate failed       -> ``regenerate`` (esc=gate)
    2. safety veto/flag                   -> ``review`` (esc=safety)
    3. jury split (agreement < min)       -> ``review`` (esc=split)
    4. degraded (missing judges)          -> ``review`` (esc=degraded)
    5. pooled confidence < threshold      -> ``review`` (esc=below_threshold)
    6. channel dial is REVIEW             -> ``review`` (esc=mode)
    7. otherwise                          -> ``auto`` (esc=none)

    Steps 2–4 are auto-blockers the pure router does not know about; they force
    review even when confidence clears the bar.
    """
    gates = gates or []
    pooled = pool_confidence(votes)
    agree = agreement(votes)

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
