"""Comment triage — classify an inbound comment and propose a reply draft.

Triage is the engagement path's judgement step. It does two things:

1. **Classify** the comment into a :class:`TriageCategory` using a deterministic,
   no-model screen (so triage itself never depends on an LLM key). The authoritative
   safety read reuses the reply cell's :func:`~cells.reply.screen_incoming` hostile/
   threat pre-screen — which can only ever *raise* the safety bar, never clear one.

2. **Propose a reply draft.** Positive / routine / question comments get an
   on-voice auto-reply candidate; negative / complaint / crisis / ambiguous comments
   still get a *neutral holding draft* for the human plus an escalation reason. The
   draft generator is injectable (hermetic tests pass a stub); the default uses the
   real reply cell when ``ANTHROPIC_API_KEY`` is set, else a brand-appropriate
   TEMPLATE clearly marked ``source="template"`` so a generated draft is never
   mistaken for a model-written one.

Everything here is advisory. The hard gate (``autonomy=HOLD`` -> review, nothing
sends) lives in :mod:`engagement.handler`; triage only enriches the action.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from autonomy.decision import SafetyVerdict
from cells.reply import screen_incoming
from engagement.ingest import CommentEvent


class TriageCategory(str, Enum):
    """What the comment is, which drives reply-vs-escalate."""

    POSITIVE = "positive"      # praise -> warm auto-reply candidate
    ROUTINE = "routine"        # thanks / acknowledgement -> light auto-reply
    QUESTION = "question"      # asking something -> informative auto-reply candidate
    NEGATIVE = "negative"      # hostile / abusive -> escalate, do not engage
    COMPLAINT = "complaint"    # dissatisfied customer -> escalate, empathetic human
    CRISIS = "crisis"          # safety / medical / legal / threat -> escalate now
    AMBIGUOUS = "ambiguous"    # intent unclear -> a human should read it first


#: Categories that ALWAYS route to a human (still drafted, with an escalation reason).
ESCALATE_CATEGORIES: frozenset[TriageCategory] = frozenset(
    {TriageCategory.NEGATIVE, TriageCategory.COMPLAINT, TriageCategory.CRISIS, TriageCategory.AMBIGUOUS}
)

# Deterministic keyword screens. Conservative by design: when several fire, crisis
# beats complaint beats question beats positive/routine (handled by check order).
_THREAT_RE = re.compile(
    r"\b(kill|hurt|find you|watch your back|regret|sue|lawyer|lawsuit|police|report you)\b",
    re.IGNORECASE,
)
_CRISIS_RE = re.compile(
    r"\b(infect(?:ed|ion)?|allerg(?:y|ic)|reaction|swollen|swelling|bleeding|pus|"
    r"refund|chargeback|scam|fraud|ripped off|emergency|hospital|medical)\b",
    re.IGNORECASE,
)
_COMPLAINT_RE = re.compile(
    r"\b(disappoint(?:ed|ing)?|terrible|awful|worst|hate it|ruin(?:ed)?|rude|"
    r"unprofessional|never again|waste|overpriced|too expensive|unhappy|angry|"
    r"botched|messed up|crooked|uneven|blurry|regret it|not happy)\b",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(
    r"(\?|\bhow much\b|\bhow do\b|\bhow long\b|\bwhen\b|\bwhere\b|\bwhat\b|\bdo you\b|"
    r"\bcan you\b|\bcould you\b|\bare you\b|\bis there\b|\bany (?:spots|slots|openings)\b|"
    r"\bavailab|\bbook\b|\bappointment\b|\bconsult|\bprice\b|\bcost\b|\bwaitlist\b)",
    re.IGNORECASE,
)
_POSITIVE_RE = re.compile(
    r"(\blove\b|\bamazing\b|\bbeautiful\b|\bgorgeous\b|\bstunning\b|\bobsessed\b|"
    r"\bperfect\b|\bincredible\b|\bwow\b|\bgreat\b|\bawesome\b|\bbest\b|\bfire\b|"
    r"😍|❤|🔥|🙌|🤩|😻)",
    re.IGNORECASE,
)
_ROUTINE_RE = re.compile(r"\b(thank|thanks|thx|ty|appreciate|cheers)\b", re.IGNORECASE)

_REASONS: dict[TriageCategory, str] = {
    TriageCategory.CRISIS: (
        "crisis: urgent safety / medical / legal / threat signal — route to a human now"
    ),
    TriageCategory.NEGATIVE: "negative: hostile or abusive comment — do not auto-engage",
    TriageCategory.COMPLAINT: "complaint: dissatisfied customer — needs an empathetic human reply",
    TriageCategory.AMBIGUOUS: "ambiguous: intent unclear — a human should read it before replying",
}


def classify_comment(text: str) -> tuple[TriageCategory, SafetyVerdict]:
    """Classify an inbound comment and return ``(category, safety_verdict)``.

    Order of precedence (first match wins): hostile/threat safety veto -> keyword
    crisis -> complaint -> question -> positive -> routine -> ambiguous.
    """
    text = (text or "").strip()
    if not text:
        return TriageCategory.AMBIGUOUS, SafetyVerdict.PASS

    # The reply cell's deterministic pre-screen is the safety authority for abuse/
    # threats; a VETO here can only escalate (never clears a reply).
    if screen_incoming(text) is SafetyVerdict.VETO:
        if _THREAT_RE.search(text):
            return TriageCategory.CRISIS, SafetyVerdict.VETO
        return TriageCategory.NEGATIVE, SafetyVerdict.VETO

    if _THREAT_RE.search(text) or _CRISIS_RE.search(text):
        # Medical / legal / refund / threat language: not necessarily abusive, but a
        # human must handle it. FLAG (not VETO) so the decision shows a safety chip.
        return TriageCategory.CRISIS, SafetyVerdict.FLAG

    if _COMPLAINT_RE.search(text):
        return TriageCategory.COMPLAINT, SafetyVerdict.PASS

    if _QUESTION_RE.search(text):
        return TriageCategory.QUESTION, SafetyVerdict.PASS

    if _POSITIVE_RE.search(text):
        return TriageCategory.POSITIVE, SafetyVerdict.PASS

    if _ROUTINE_RE.search(text):
        return TriageCategory.ROUTINE, SafetyVerdict.PASS

    return TriageCategory.AMBIGUOUS, SafetyVerdict.PASS


@dataclass(frozen=True)
class ReplyProposal:
    """A proposed reply draft plus its provenance (model vs template)."""

    text: str
    source: str  # "reply_cell" (LLM, on-voice) | "template" (deterministic fallback)


@dataclass(frozen=True)
class TriageResult:
    """The triage outcome for one comment: how it was classified, the proposed
    draft, and (when it should not auto-reply) why it escalates."""

    category: TriageCategory
    escalate: bool
    escalation_reason: str | None
    safety_verdict: SafetyVerdict
    reply: str
    reply_source: str


# A reply generator: given the comment + its category, return a draft proposal.
# Injectable so triage runs hermetically (no network / no LLM key) under test.
ReplyGenerator = Callable[[CommentEvent, TriageCategory], ReplyProposal]


def _template_reply(event: CommentEvent, category: TriageCategory) -> str:
    """A brand-appropriate, deterministic fallback reply (no AI tells, short)."""
    who = f"@{event.author}" if event.author and event.author != "unknown" else "Hi"
    if category is TriageCategory.QUESTION:
        return f"{who} great question! DM us and we'll get you all the details. 💛"
    if category is TriageCategory.POSITIVE:
        return f"{who} thank you so much, this means a lot! 🙏"
    if category is TriageCategory.ROUTINE:
        return f"{who} thank you! 🙏"
    # Escalations get a neutral holding draft the human can send or rework.
    return (
        f"{who} thank you for reaching out. We want to make this right, so a team "
        f"member will follow up with you directly."
    )


def make_default_reply_generator(
    *, brand_voice_context: str = "", approved_claims: tuple[str, ...] = ()
) -> ReplyGenerator:
    """Build the default reply generator.

    Uses the real reply cell (on-voice, S2 brand-voice + S3 AI-flagger) when an
    ``ANTHROPIC_API_KEY`` is configured; otherwise returns a clearly-marked TEMPLATE
    draft. Any cell failure (missing/expired key, validation, network) falls back to
    the template rather than dropping the action.
    """

    def generate(event: CommentEvent, category: TriageCategory) -> ReplyProposal:
        if os.environ.get("ANTHROPIC_API_KEY"):
            try:
                from cells.reply import build_reply_cell

                cell = build_reply_cell(
                    brand_voice_context=brand_voice_context, approved_claims=approved_claims
                )
                prompt = (
                    "surface: comment\n"
                    f"incoming comment from @{event.author}: {event.text}"
                )
                draft = cell.run_sync(prompt)
                return ReplyProposal(text=draft.text, source="reply_cell")
            except Exception:  # noqa: BLE001 — never let a model issue drop the draft
                pass
        return ReplyProposal(text=_template_reply(event, category), source="template")

    return generate


def triage_comment(
    event: CommentEvent,
    *,
    reply_generator: ReplyGenerator | None = None,
    brand_voice_context: str = "",
    approved_claims: tuple[str, ...] = (),
) -> TriageResult:
    """Classify ``event`` and attach a proposed reply draft + escalation reason."""
    category, safety = classify_comment(event.text)
    escalate = category in ESCALATE_CATEGORIES or safety is not SafetyVerdict.PASS
    reason = _REASONS.get(category) if escalate else None

    generator = reply_generator or make_default_reply_generator(
        brand_voice_context=brand_voice_context, approved_claims=approved_claims
    )
    proposal = generator(event, category)

    return TriageResult(
        category=category,
        escalate=escalate,
        escalation_reason=reason,
        safety_verdict=safety,
        reply=proposal.text,
        reply_source=proposal.source,
    )
