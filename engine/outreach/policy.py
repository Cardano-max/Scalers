"""Outreach policy (bead 1mk.7) — composes the gates into one auditable plan.

Order is load-bearing (spec §5 + dos-donts: suppression-first, deliverability
verification, capped personalized sequence, reply/bounce hard-stop, no creepy
personalization, NEVER auto-send):

  1. **Suppression-first** — on the do-not-contact list? → SKIP, build nothing.
  2. **Hard-stop** — reply/bounce/unsubscribe/complaint seen? → HARD_STOP, halt.
  3. **Deliverability verify** — undeliverable → BLOCK; risky → escalate + warn.
  4. **Over-personalization guard** — strip creepy signals, cap refs/touch.
  5. **Sequence** — capped 4-touch (day 0/+3/+5/+7), warmup-aware cap, RFC-8058
     unsubscribe on every touch.
  6. **Route to review** — Disposition.ESCALATE, ``routed_to="review"``. Under
     bead 439 nothing auto-sends; ``plan.will_send`` is always False.

The per-touch *copy* is generated downstream by the writer's copywriter cell
(1mk.5) grounded in brand-voice (S2) and checked by the S3 AI-flagger validator +
jury — this policy governs structure, eligibility, and safety, not prose.
"""

from __future__ import annotations

from autonomy.decision import Escalation, EscKind

from outreach.personalization import brief_for_touch, screen_signals
from outreach.schema import (
    MAX_TOUCHES,
    Disposition,
    OutreachPlan,
    Prospect,
    StopReason,
)
from outreach.sequence import SequencePlanner
from outreach.suppression import SuppressionGate
from outreach.verifier import DeliverabilityVerifier


class OutreachPolicy:
    """Build a safe, suppression-first, capped, escalate-only outreach plan."""

    def __init__(
        self,
        *,
        suppression: SuppressionGate | None = None,
        verifier: DeliverabilityVerifier | None = None,
        planner: SequencePlanner | None = None,
    ) -> None:
        self._suppression = suppression or SuppressionGate()
        self._verifier = verifier or DeliverabilityVerifier()
        self._planner = planner or SequencePlanner()

    def plan(
        self, prospect: Prospect, *, events: tuple[StopReason, ...] = ()
    ) -> OutreachPlan:
        ref = prospect.ref

        # 1. Suppression-first.
        supp = self._suppression.check(prospect)
        if supp.suppressed:
            return OutreachPlan(
                prospect_ref=ref, disposition=Disposition.SKIP_SUPPRESSED,
                suppression=supp, notes=(supp.reason or "suppressed",),
                escalation=Escalation(kind=EscKind.GATE, label="suppression: do-not-contact"),
            )

        # 2. Hard-stop on reply/bounce/unsubscribe/complaint.
        if self._planner.stop_index(events) is not None:
            return OutreachPlan(
                prospect_ref=ref, disposition=Disposition.HARD_STOP,
                suppression=supp,
                notes=(f"hard-stop on {','.join(events)}",),
                escalation=Escalation(kind=EscKind.GATE, label=f"hard-stop: {','.join(events)}"),
            )

        # 3. Deliverability verification.
        verdict = self._verifier.verify(prospect.email)
        if verdict.status == "undeliverable":
            return OutreachPlan(
                prospect_ref=ref, disposition=Disposition.BLOCK_UNDELIVERABLE,
                suppression=supp, verification=verdict, notes=verdict.reasons,
                escalation=Escalation(kind=EscKind.GATE, label="deliverability: undeliverable"),
            )

        # 4. Over-personalization guard.
        guard = screen_signals(prospect.signals)
        briefs = [brief_for_touch(guard.allowed, i + 1) for i in range(MAX_TOUCHES)]

        # 5. Capped, spaced sequence.
        sequence = self._planner.build(allowed_briefs=briefs, events=events)

        warnings = list(guard.warnings)
        if verdict.status == "risky":
            warnings.append("deliverability risky — human review before any send")

        notes = ["routed to review (439 hold: no auto-send)"]
        if not guard.allowed:
            notes.append("no safe personalization signals — generic on-voice copy")

        # 6. Escalate (always; 439). MODE = the channel dial forces approve-first
        # (shared EscKind vocab with the reply engine's escalation chip).
        return OutreachPlan(
            prospect_ref=ref, disposition=Disposition.ESCALATE,
            suppression=supp, verification=verdict, sequence=sequence,
            warnings=tuple(warnings), notes=tuple(notes),
            escalation=Escalation(kind=EscKind.MODE, label="outreach approve-first (439 hold)"),
        )
