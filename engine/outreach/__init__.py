"""Outreach engine (bead 1mk.7) — suppression-first, verified, capped, escalate-only.

The outreach skills (outreach-sequence-builder + cold-email-verifier) adopted as
deterministic in-house enforcement: suppression-first gate, deliverability
verifier (broker-enrichment/auto-send STRIPPED), capped 4-touch sequence with
warmup + hard-stop, over-personalization guard. Composed by :class:`OutreachPolicy`
into an auditable, PII-free, review-routed plan — NEVER auto-send (bead 439 hold).
"""

from autonomy.decision import Escalation, EscKind
from harness.state import RouteDecision

from outreach.personalization import GuardResult, screen_signals
from outreach.policy import OutreachPolicy
from outreach.schema import (
    MAX_TOUCHES,
    TOUCH_DAY_OFFSETS,
    Disposition,
    OutreachPlan,
    OutreachSequence,
    Prospect,
    SuppressionResult,
    Touch,
    VerificationVerdict,
    prospect_ref,
)
from outreach.sequence import SequencePlanner, cap_per_inbox_day
from outreach.suppression import SuppressionGate
from outreach.verifier import DeliverabilityVerifier, verify_email

__all__ = [
    "OutreachPolicy",
    "OutreachPlan",
    "OutreachSequence",
    "Touch",
    "Prospect",
    "Disposition",
    "SuppressionResult",
    "VerificationVerdict",
    "prospect_ref",
    "MAX_TOUCHES",
    "TOUCH_DAY_OFFSETS",
    "SuppressionGate",
    "DeliverabilityVerifier",
    "verify_email",
    "SequencePlanner",
    "cap_per_inbox_day",
    "GuardResult",
    "screen_signals",
    "Escalation",
    "EscKind",
    "RouteDecision",
]
