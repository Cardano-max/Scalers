"""Reply cell — short social comment/DM replies (skill: reply, CustomerAcq-1mk.6,
Tier-3).

Adapts sales **discovery** (ask before you pitch) + **objection-handling** to
*short social replies* in the artist's voice, with a strict human-handoff policy.

THE HARD RULE (non-negotiable, enforced in code two ways):
  * **Comments** may auto-reply *within the confidence threshold* (the normal
    autonomy gate).
  * **DMs ALWAYS route to a human** — no matter the confidence. A DM reply is
    never auto-sent.

Two enforcement points so the rule cannot be bypassed:
  1. *Cell boundary* — :func:`reply_validators` makes ``surface == DM`` with
     ``recommend_escalate == False`` an ERROR (the draft is repaired until the
     model marks the DM for handoff).
  2. *Routing* — :func:`route_reply` returns ``REVIEW`` for every DM regardless
     of confidence, layered on the canonical pure-code router for comments.

Composition (conditionally loaded by the harness on the engagement cell):
  * **S2 brand-voice** — replies are written in the artist's voice (context
    injected into instructions; approved claims only).
  * **S3 AI-flagger** — :func:`~cells.ai_flagger.detect_ai_tells` runs over the
    reply text as an ERROR validator (no AI slop in public replies).

A deterministic hostile/troll pre-screen (:func:`screen_incoming`) can only *add*
a safety escalation — it never green-lights a reply. The authoritative safety
verdict is the AUTON-04 jury; the release gate is reply-safety = 0 violations on
the red-team (rvy.7/.8).

Re-authored from louisblythe/Sales-Skills (discovery + objection-handling),
retargeted to short social replies; see engine/skills/reply/. Registration for
agent use is gated on 1mk.1 sec-vetting + the eval gate (autonomy hold, bead 439).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, Field

from autonomy.decision import EscKind, Escalation, SafetyVerdict
from cells.ai_flagger import FlaggerConfig, ai_flagger
from cells.base import Cell
from cells.validators import (
    FieldValidator,
    Severity,
    ValidationIssue,
    ValidatorBank,
    banned_phrases,
    no_placeholder,
    non_empty,
    word_count_between,
)
from harness.router import DEFAULT_THRESHOLD, route
from harness.state import AutonomyMode, RouteDecision

# A public reply is short. Beyond this it stops reading like a person in a thread.
_REPLY_MAX_WORDS = 45


class ReplySurface(str, Enum):
    """Where the reply will be posted. Drives the handoff policy."""

    COMMENT = "comment"   # public; may auto within threshold
    DM = "dm"             # private; ALWAYS routes to a human


class ReplyIntent(str, Enum):
    """What the reply is doing — discovery, or which objection it handles."""

    DISCOVERY = "discovery"        # ask a question to understand the need first
    PRICE = "price"
    PAIN = "pain"
    HEALING = "healing"
    BOOKING = "booking"            # process / availability
    DESIGN_DOUBT = "design_doubt"  # "will my idea work as a tattoo?"
    COMPARISON = "comparison"      # "why you vs another artist"
    TRUST = "trust"
    OTHER = "other"
    NONE = "none"                  # simple thanks / acknowledgement


class ReplyDraft(BaseModel):
    """A short, on-voice reply plus the routing signals the harness needs."""

    surface: ReplySurface = Field(description="comment (may auto) or dm (always human).")
    text: str = Field(description="The short reply, in the artist's voice.")
    intent: ReplyIntent = Field(description="Discovery, or which objection this handles.")
    discovery_question: str = Field(
        default="", description="Optional single question to learn more before pitching."
    )
    needs_human_expertise: bool = Field(
        default=False,
        description="True if the question needs human/medical/safety/legal judgement -> escalate.",
    )
    recommend_escalate: bool = Field(
        description="Cell's handoff recommendation. MUST be true for any DM.",
    )
    escalation_reason: str = Field(default="", description="Why it should escalate, if it should.")


# --------------------------------------------------------------------------- #
# Validators — including the cell-boundary half of the HARD RULE
# --------------------------------------------------------------------------- #


def dm_requires_escalation() -> FieldValidator:
    """HARD RULE (cell boundary): a DM reply must be marked for human handoff."""

    def _fn(value):
        surface = getattr(value, "surface", None)
        escalate = getattr(value, "recommend_escalate", None)
        if surface is ReplySurface.DM and not escalate:
            return [ValidationIssue(
                "dm_requires_escalation", Severity.ERROR,
                "DM reply must set recommend_escalate=true (DMs always route to a human)")]
        return []

    return FieldValidator("dm_requires_escalation", _fn)


def expertise_requires_escalation() -> FieldValidator:
    """A reply that needs human expertise must also be marked for handoff."""

    def _fn(value):
        if getattr(value, "needs_human_expertise", False) and not getattr(value, "recommend_escalate", False):
            return [ValidationIssue(
                "expertise_requires_escalation", Severity.ERROR,
                "needs_human_expertise=true requires recommend_escalate=true")]
        return []

    return FieldValidator("expertise_requires_escalation", _fn)


def reply_validators(*, config: FlaggerConfig = FlaggerConfig()) -> ValidatorBank:
    """The deterministic gates a reply draft must clear."""
    return ValidatorBank(validators=(
        non_empty("text"),
        no_placeholder("text"),
        banned_phrases("text"),
        word_count_between("text", 1, _REPLY_MAX_WORDS),
        ai_flagger("text", config),          # S3
        dm_requires_escalation(),            # HARD RULE (boundary)
        expertise_requires_escalation(),
    ))


# --------------------------------------------------------------------------- #
# Deterministic hostile/troll pre-screen (can only ESCALATE, never approve)
# --------------------------------------------------------------------------- #

# Conservative signals. This is a pre-screen, NOT the authority: it can only push
# a reply to human review (VETO/FLAG); it can never clear one for auto-send. The
# AUTON-04 cross-family jury is the safety authority; this just guarantees the
# obvious cases never slip to auto.
_HOSTILE_RE = re.compile(
    r"\b(kill yourself|kys|fuck you|f\*ck you|stfu|shut up|idiot|moron|loser|"
    r"scam(?:mer)?|fraud|disgusting|hideous|trash|garbage|hate you|"
    r"you suck|worst)\b",
    re.IGNORECASE,
)
_THREAT_RE = re.compile(r"\b(i'?ll\s+(?:find|hurt|kill)|watch your back|you'?ll regret)\b", re.IGNORECASE)


def screen_incoming(comment_text: str) -> SafetyVerdict:
    """Deterministic pre-screen of an INCOMING comment/DM (pure code, no model).

    Returns ``VETO`` on clear threats/abuse, ``FLAG`` on milder hostility, else
    ``PASS``. Only ever *raises* the safety bar — a ``PASS`` here is not a safety
    approval, just "no obvious hostility"; the jury still decides.
    """
    if not comment_text:
        return SafetyVerdict.PASS
    if _THREAT_RE.search(comment_text):
        return SafetyVerdict.VETO
    if _HOSTILE_RE.search(comment_text):
        return SafetyVerdict.VETO
    return SafetyVerdict.PASS


# --------------------------------------------------------------------------- #
# Routing — the HARD RULE + safety, layered on the canonical pure-code router
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReplyRouting:
    """The routing outcome for one reply (feeds the autonomy DecisionRecord)."""

    decision: RouteDecision
    escalation: Escalation

    @property
    def auto(self) -> bool:
        return self.decision is RouteDecision.AUTO


def route_reply(
    draft: ReplyDraft,
    *,
    confidence: float,
    threshold: float = DEFAULT_THRESHOLD,
    autonomy: AutonomyMode = AutonomyMode.AUTO,
    safety: SafetyVerdict = SafetyVerdict.PASS,
) -> ReplyRouting:
    """Decide how a reply routes. DMs ALWAYS go to a human; comments use the gate.

    Precedence (first match wins):
      1. surface is DM            -> REVIEW (handoff policy) — even at confidence 1.0
      2. safety not PASS          -> REVIEW (hostile/troll veto/flag)
      3. needs_human_expertise    -> REVIEW (handoff)
      4. otherwise (a comment)    -> the canonical router (confidence/gate/dial)
    """
    # 1. THE HARD RULE: a DM is never auto-sent.
    if draft.surface is ReplySurface.DM:
        return ReplyRouting(RouteDecision.REVIEW,
                            Escalation(kind=EscKind.MODE, label="DM always routes to a human"))

    # 2. Safety veto/flag (deterministic pre-screen or jury).
    if safety is not SafetyVerdict.PASS:
        return ReplyRouting(RouteDecision.REVIEW,
                            Escalation(kind=EscKind.SAFETY, label=f"safety {safety.value}"))

    # 3. Explicit human-expertise handoff.
    if draft.needs_human_expertise:
        return ReplyRouting(RouteDecision.REVIEW,
                            Escalation(kind=EscKind.MODE, label="needs human expertise (handoff)"))

    # 4. Comment: the normal confidence/gate/dial decision.
    base = route(confidence, threshold, [], autonomy)
    if base is RouteDecision.AUTO:
        return ReplyRouting(RouteDecision.AUTO, Escalation(kind=EscKind.NONE, label="comment auto within threshold"))
    if base is RouteDecision.REGENERATE:
        return ReplyRouting(base, Escalation(kind=EscKind.GATE, label="deterministic gate failed"))
    kind = EscKind.BELOW_THRESHOLD if confidence < threshold else EscKind.MODE
    return ReplyRouting(RouteDecision.REVIEW, Escalation(kind=kind, label="comment below auto bar / dial"))


# --------------------------------------------------------------------------- #
# Instructions (S2 brand-voice composed in)
# --------------------------------------------------------------------------- #

_BASE_RULES = (
    "You are the artist's engagement assistant, writing a SHORT reply to a social "
    "COMMENT or DM. One thought, in the artist's voice, like a real person in the "
    "thread — not a brand account.\n"
    "How to reply:\n"
    "- DISCOVERY first: when it helps, ask ONE question to understand what they "
    "want before suggesting anything. Don't pitch into a vague comment.\n"
    "- OBJECTION-HANDLING: acknowledge the concern, answer honestly using only "
    "Approved claims, then offer a soft next step. Never argue or get defensive.\n"
    "HARD RULES:\n"
    "- If 'surface' is a DM, set recommend_escalate=true. DMs ALWAYS go to a human "
    "— draft a helpful reply for the human to send, but never imply it auto-sends.\n"
    "- If the question needs human, medical, safety, or legal judgement (healing "
    "problems, allergic reactions, pain/medical advice, pricing commitments, legal/"
    "consent issues), set needs_human_expertise=true and recommend_escalate=true.\n"
    "- If the comment is hostile, a troll, or abusive, do NOT engage — set "
    "recommend_escalate=true and keep any text neutral.\n"
    "- Only state Approved claims; never invent offers, prices, or guarantees.\n"
    "- No AI tells (no em-dashes, contrast framing, rule-of-three, generic "
    "transitions). No placeholders. Keep it short."
)


def build_reply_instructions(brand_voice_context: str = "", approved_claims: tuple[str, ...] = ()) -> str:
    """Assemble reply-cell instructions, composing the S2 brand-voice context in."""
    parts: list[str] = []
    if brand_voice_context.strip():
        parts += ["# BRAND VOICE (source of truth — reply as this artist):",
                  brand_voice_context.strip(), ""]
    if approved_claims:
        parts += ["# Approved claims (the ONLY claims you may state):"]
        parts += [f"- {c}" for c in approved_claims]
        parts += [""]
    parts += [_BASE_RULES]
    return "\n".join(parts)


def build_reply_cell(
    *,
    brand_voice_context: str = "",
    approved_claims: tuple[str, ...] = (),
    config: FlaggerConfig = FlaggerConfig(),
    **overrides,
) -> Cell[ReplyDraft]:
    """Build the reply cell (short social comment/DM replies).

    The harness assembles ``brand_voice_context`` + ``approved_claims`` per tenant
    and routes the output through :func:`route_reply` (DMs always to a human).
    """
    params = dict(
        name="reply",
        schema=ReplyDraft,
        instructions=build_reply_instructions(brand_voice_context, approved_claims),
        validators=reply_validators(config=config),
    )
    params.update(overrides)
    return Cell(**params)
