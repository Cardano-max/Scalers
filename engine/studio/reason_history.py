"""reason_history — evidence-grounded objection / intent extraction from a lead's
prior conversation. This is the signal layer the psychology analyst reasons over.

It is a small, honest, aspect-based-sentiment-style (ABSA) extractor: rather than one
blob sentiment, it pulls FINE-GRAINED signals (an objection *type* with the customer's
own words as the opinion span, style/subject interest, artist mention, channel,
urgency) from the CUSTOMER turns only. Grounded in the public ABSA framing where a read
is an (aspect, opinion-term, polarity) triple [systematic review of ABSA, arXiv
2311.10777] and the standard sales-objection taxonomy — price/budget, timing/urgency,
trust/risk, need-value/uncertainty, payment [prospeo.io, highspot, salesforce
objection-handling guides].

HARD ANTI-FABRICATION (the project's #1 gate): every signal carries the VERBATIM
customer quote it was drawn from (``evidence``). Nothing is emitted without a real span.
No conversation -> :func:`extract_signals` returns an honest empty result
(``has_conversation=False``); it NEVER invents an objection, a style, or an intent.

Pure functions only (no DB, no model) so the extraction is fully unit-testable. The
psychology analyst (:mod:`studio.psych_profile`) consumes this; the conversation itself
is stored by :mod:`studio.conversations` and surfaced through the message-source
adapters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from studio.conversations import (
    SPEAKER_CUSTOMER,
    SPEAKER_STUDIO,
    normalize_turns,
)

# --------------------------------------------------------------------------- #
# Objection taxonomy — the canonical types + the trigger phrases that GROUND each.
# Ordered most-specific-first so "maybe later" reads as timing, not bare uncertainty,
# and "payment plan" reads as payment, not price. Every phrase is a literal the
# customer actually has to have written for the objection to be emitted.
#
# Beyond the customer-phrase types, two reads come from the REAL thread rather than a
# customer phrase — still verbatim-quoted, never invented:
#   * ``blocked_by_prereq`` — a concrete prerequisite stops the booking (laser removal,
#     healing, a consult first); the quote may be the studio turn stating it.
#   * ``went_quiet_mid_booking`` — the lead was actively booking and stopped responding
#     at a concrete step; the quote is the studio's unanswered ask (the exact step).
# --------------------------------------------------------------------------- #
OBJECTION_PRICE = "price"
OBJECTION_TIMING = "timing"
OBJECTION_TRUST = "trust"
OBJECTION_UNCERTAINTY = "uncertainty"
OBJECTION_PAYMENT = "payment"
OBJECTION_TRUST_CONCERN = "trust_concern"
OBJECTION_BLOCKED_PREREQ = "blocked_by_prereq"
OBJECTION_WENT_QUIET = "went_quiet_mid_booking"

# (type, [phrases]) — checked in this order; the first type that matches a turn wins for
# that turn (a turn can still contribute a secondary type on a different phrase).
_OBJECTION_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # A TRUST BREACH from a bad prior experience with US (reschedules, refund/dispute
    # language, review threats, lost confidence) outranks everything: "refund of my
    # deposit" must read as trust_concern, never as a payment objection on "deposit".
    (OBJECTION_TRUST_CONCERN, (
        "refund", "dispute the charge", "dispute with my bank", "chargeback",
        "charge back", "no longer confident", "not confident", "do not have confidence",
        "don't have confidence", "dont have confidence", "lost confidence",
        "no confidence", "writing a review", "write a review", "bad experience",
        "never coming back", "moved my schedule around", "reschedule again",
        "rescheduled again",
    )),
    (OBJECTION_PAYMENT, (
        "payment plan", "installment", "instalment", "pay later", "pay it off",
        "deposit", "split the", "afterpay", "klarna", "financing", "pay in",
    )),
    # Price OBJECTION phrases only — genuine resistance. A bare "how much?" is a
    # buying-signal INQUIRY, not an objection, so it is deliberately excluded here (the
    # analyst can still read price-curiosity separately); the objection must trace to
    # real resistance ("short on budget"), never to a price question.
    (OBJECTION_PRICE, (
        "short on budget", "on a budget", "tight budget", "budget", "afford",
        "too expensive", "expensive", "too much", "pricey", "out of my range",
        "cheaper", "save up", "a bit much", "steep", "can't spend", "cant spend",
    )),
    (OBJECTION_TIMING, (
        "maybe later", "not right now", "right now", "next month", "next year",
        "after the", "some other time", "down the road", "hold off", "put it off",
        "too busy", "no time", "when things settle", "later on", "in a few",
        "not this", "eventually",
    )),
    (OBJECTION_TRUST, (
        "first tattoo", "nervous", "scared", "worried", "hesitant", "second thoughts",
        "is it safe", "hygiene", "clean", "reviews", "reputation", "portfolio",
        "see more of", "healed", "how experienced", "trust",
    )),
    (OBJECTION_UNCERTAINTY, (
        "not sure", "unsure", "still deciding", "still thinking", "thinking about it",
        "on the fence", "haven't decided", "don't know if", "not certain", "undecided",
        "maybe", "might",
    )),
)

# Specificity rank for the PRIMARY pick: taxonomy order, with the thread-shape reads
# (prereq / went-quiet) ranked after every customer-stated type — the customer's own
# words always outrank a read derived from the studio's turns or the thread shape.
_OBJECTION_RANK: dict[str, int] = {t: i for i, (t, _) in enumerate(_OBJECTION_PHRASES)}
_OBJECTION_RANK[OBJECTION_BLOCKED_PREREQ] = len(_OBJECTION_RANK)
_OBJECTION_RANK[OBJECTION_WENT_QUIET] = len(_OBJECTION_RANK)

# A prerequisite that concretely blocks booking (stated in the thread — often by the
# studio: "laser removal sessions prior to covering up"). Deliberately narrow: a benign
# "send healed photos" or "trimmed prior to coming in" must NOT read as a blocker.
_PREREQ_PHRASES = (
    "laser removal", "laser session", "removal sessions", "tattoo removal",
    "removal first", "needs to heal", "fully healed before", "heal first",
    "consultation first", "consult first", "consultation before", "consult before",
    "that process first",
)

# Booking-intent cues — went-quiet requires the lead to have been ACTIVELY booking.
_BOOKING_INTENT = (
    "ready to", "ready for", "i'm ready", "im ready", "book", "deposit",
    "appointment", "how much", "price", "looking for", "looking to get",
    "i'd like", "id like", "would like", "available", "availability",
    "what days", "come in", "get in",
)
# A final customer message that is an opt-out is a STATED choice, not going quiet.
_OPT_OUT_WORDS = ("stop", "unsubscribe", "opt out", "opt-out", "do not text",
                  "don't text", "dont text", "remove me")
# The unanswered studio ask that marks the concrete step the thread stopped at.
_REQUEST_MARKERS = ("?", "please", "send", "let me know", "would you", "could you",
                    "can you")

# Urgency read — high (a near-term commitment window) vs low (an explicit deferral).
_URGENCY_HIGH = (
    "this week", "this weekend", "asap", "as soon as", "right away", "today",
    "tomorrow", "soon", "ready to book", "want to book", "let's book", "book it",
)
_URGENCY_LOW = (
    "maybe later", "not right now", "some day", "someday", "eventually", "no rush",
    "down the road", "next year", "in the future",
)

# A curated tattoo style / subject / placement lexicon (grounded interest signal).
# These are craft terms a customer uses to describe what they want — real, not inferred.
_STYLE_TERMS = (
    "fine-line", "fine line", "fineline", "floral", "flower", "botanical", "bold",
    "traditional", "neo-traditional", "neotraditional", "blackwork", "realism",
    "watercolor", "watercolour", "minimalist", "minimal", "script", "lettering",
    "geometric", "mandala", "dotwork", "linework", "portrait", "sleeve", "wrist",
    "forearm", "ankle", "shoulder", "back piece", "cover-up", "cover up", "coverup",
    "flash", "snake", "butterfly", "rose", "small piece", "micro",
)

_CHANNEL_TERMS = {
    "text": "sms", "sms": "sms", "message": "sms", "dm": "instagram",
    "instagram": "instagram", "insta": "instagram", "email": "email",
    "whatsapp": "whatsapp", "call": "phone", "phone": "phone",
}


@dataclass(frozen=True)
class Signal:
    """One fine-grained extracted signal, always grounded on a verbatim thread span."""

    value: str
    evidence: str  # the exact turn text the signal was drawn from (verbatim)
    # Whose turn the evidence quotes: "customer" (their own words -> a stated read) or
    # "studio" (a prereq / unanswered ask -> derived, never asserted as their words).
    source: str = SPEAKER_CUSTOMER


@dataclass(frozen=True)
class ReasonSignals:
    """The structured, evidence-grounded read of a lead's conversation.

    Every list is empty (and ``has_conversation`` False) when there is no conversation
    to reason over — an honest blank, never a fabricated signal."""

    has_conversation: bool = False
    objections: list[Signal] = field(default_factory=list)  # strongest-first, de-duped by type
    styles: list[Signal] = field(default_factory=list)
    artists: list[Signal] = field(default_factory=list)
    urgency: Signal | None = None
    channel: str | None = None
    last_customer_message: str | None = None
    n_customer_turns: int = 0

    @property
    def primary_objection(self) -> Signal | None:
        return self.objections[0] if self.objections else None

    def objection_types(self) -> list[str]:
        return [o.value for o in self.objections]


# --------------------------------------------------------------------------- #
# Transcript parsing — accept the operator's real shapes, honestly.
# --------------------------------------------------------------------------- #
_SPEAKER_RE = re.compile(r"^\s*(customer|client|lead|studio|shop|us|me|you)\s*:\s*(.*)$", re.I)
_CUSTOMER_LABELS = {"customer", "client", "lead"}
_STUDIO_LABELS = {"studio", "shop", "us", "me"}


def parse_conversation_text(raw: str) -> list[dict[str, str]]:
    """Parse an uploaded transcript into ``[{speaker, text}]`` turns.

    Accepts the operator's real shapes: ``Customer:`` / ``Studio:`` labelled lines,
    turns separated by newlines OR by ``/`` (his SMS-thread sample uses slashes). Lines
    without a speaker label attach to the previous speaker's turn (a wrapped message).
    Honest: returns ``[]`` for empty / unlabelled input rather than guessing a dialogue.
    """
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    # The operator's sample separates turns with " / "; treat a slash between a period/word
    # and a capitalized speaker label as a turn break, without shattering URLs or prices.
    segments: list[str] = []
    for line in text.split("\n"):
        # split on " / " only when it precedes a speaker label, so "$120/180" is safe.
        parts = re.split(r"\s+/\s+(?=(?:customer|client|lead|studio|shop|us|me)\s*:)", line, flags=re.I)
        segments.extend(p for p in (s.strip() for s in parts) if p)

    turns: list[dict[str, str]] = []
    for seg in segments:
        m = _SPEAKER_RE.match(seg)
        if m:
            label = m.group(1).lower()
            body = m.group(2).strip()
            speaker = SPEAKER_CUSTOMER if label in _CUSTOMER_LABELS else SPEAKER_STUDIO
            if body:
                turns.append({"speaker": speaker, "text": body})
        elif turns:
            # A continuation line (no label) — append to the running turn.
            turns[-1] = {
                "speaker": turns[-1]["speaker"],
                "text": (turns[-1]["text"] + " " + seg).strip(),
            }
        # A leading unlabelled line with no prior turn is ambiguous -> dropped (no guess).
    return normalize_turns(turns)


def _match_phrase(low_text: str, phrases: tuple[str, ...]) -> str | None:
    for p in phrases:
        if p in low_text:
            return p
    return None


def _first_objection_for_turn(low_text: str) -> str | None:
    """The single strongest objection TYPE evidenced in one customer turn (taxonomy
    order = specificity order), or None when the turn voices no objection."""
    for otype, phrases in _OBJECTION_PHRASES:
        if _match_phrase(low_text, phrases) is not None:
            return otype
    return None


def extract_signals(
    turns: list[dict[str, Any]] | None,
    *,
    known_artists: list[str] | None = None,
) -> ReasonSignals:
    """Extract the evidence-grounded :class:`ReasonSignals` from conversation ``turns``.

    Reads CUSTOMER turns only (the lead's own words are the opinion spans). Each
    objection/style/artist/urgency signal carries the verbatim turn it was drawn from.
    De-dupes objections by type (keeping the first, strongest evidence) but preserves
    taxonomy order so ``objections[0]`` is the primary. Honest-empty when there is no
    customer turn to read — never a fabricated signal.

    ``known_artists`` (real artist names from the artist source) lets a bare first-name
    mention resolve to a real artist; without it, only capitalized standalone name-like
    tokens after "with"/"by" are considered, conservatively."""
    norm = normalize_turns(turns)
    cust_turns = [t for t in norm if t["speaker"] == SPEAKER_CUSTOMER]
    if not cust_turns:
        return ReasonSignals(has_conversation=bool(norm))

    objections: list[Signal] = []
    seen_types: set[str] = set()
    styles: list[Signal] = []
    seen_styles: set[str] = set()
    artists: list[Signal] = []
    seen_artists: set[str] = set()
    urgency: Signal | None = None
    channel: str | None = None
    artist_pool = [a.strip().lower() for a in (known_artists or []) if a and a.strip()]

    for t in cust_turns:
        raw = t["text"]
        low = raw.lower()

        # Objection: allow one turn to contribute multiple distinct types (e.g. price +
        # timing in "I like it but maybe later, short on budget"), each grounded on this
        # same turn text. Iterate the taxonomy so ordering stays specificity-first.
        for otype, phrases in _OBJECTION_PHRASES:
            if otype in seen_types:
                continue
            if _match_phrase(low, phrases) is not None:
                objections.append(Signal(value=otype, evidence=raw))
                seen_types.add(otype)

        # Style / subject interest (real craft terms only).
        for term in _STYLE_TERMS:
            if term in low and term not in seen_styles:
                styles.append(Signal(value=term, evidence=raw))
                seen_styles.add(term)

        # Artist mention: a real known-artist name, or a name after "with"/"by".
        for a in artist_pool:
            if a and a in low and a not in seen_artists:
                artists.append(Signal(value=a, evidence=raw))
                seen_artists.add(a)
        for m in re.finditer(r"\b(?:with|by|see|book)\s+([A-Z][a-z]+)\b", raw):
            cand = m.group(1)
            if cand.lower() not in seen_artists and cand.lower() not in _STUDIO_LABELS:
                if not artist_pool or cand.lower() in artist_pool:
                    artists.append(Signal(value=cand, evidence=raw))
                    seen_artists.add(cand.lower())

        # Urgency — low deferral beats high (an explicit "maybe later" is the real read
        # even if the turn also contains a soft "soon"); only set once (first evidence).
        if urgency is None:
            if _match_phrase(low, _URGENCY_LOW) is not None:
                urgency = Signal(value="low", evidence=raw)
            elif _match_phrase(low, _URGENCY_HIGH) is not None:
                urgency = Signal(value="high", evidence=raw)

        # Channel preference the customer states ("message me", "text me").
        if channel is None:
            for term, canon in _CHANNEL_TERMS.items():
                if term in low:
                    channel = canon
                    break

    # BLOCKED-BY-PREREQUISITE — a concrete prerequisite stated anywhere in the REAL
    # thread (usually a studio turn: "laser removal sessions prior to covering up").
    # The evidence is that verbatim turn; its speaker is recorded so the psych layer
    # marks a studio-quoted read inferred, never the customer's own stated words.
    if OBJECTION_BLOCKED_PREREQ not in seen_types:
        for t in norm:
            if _match_phrase(t["text"].lower(), _PREREQ_PHRASES) is not None:
                objections.append(Signal(value=OBJECTION_BLOCKED_PREREQ,
                                         evidence=t["text"], source=t["speaker"]))
                seen_types.add(OBJECTION_BLOCKED_PREREQ)
                break

    # WENT-QUIET-MID-BOOKING — a thread-shape read, the explanation of LAST resort:
    # emitted ONLY when no other objection explains the stall, the lead showed real
    # booking intent, their final message was not an opt-out, and the thread ends with
    # >= 2 unanswered studio turns, at least one carrying a concrete ask (the exact
    # step they stopped at — that verbatim studio turn is the evidence). Anything less
    # stays honestly unlabelled rather than a guessed "ghosting".
    if not objections:
        last_low = cust_turns[-1]["text"].lower()
        intent = any(
            _match_phrase(t["text"].lower(), _BOOKING_INTENT) for t in cust_turns
        )
        opted_out = _match_phrase(last_low, _OPT_OUT_WORDS) is not None
        last_cust_idx = max(
            i for i, t in enumerate(norm) if t["speaker"] == SPEAKER_CUSTOMER
        )
        trailing = norm[last_cust_idx + 1:]
        if intent and not opted_out and len(trailing) >= 2:
            pending = next(
                (t for t in trailing
                 if any(m in t["text"].lower() for m in _REQUEST_MARKERS)),
                None,
            )
            if pending is not None:
                objections.append(Signal(value=OBJECTION_WENT_QUIET,
                                         evidence=pending["text"],
                                         source=SPEAKER_STUDIO))

    # Primary = the most specific type present (the taxonomy's documented contract):
    # stable-sorted so ties keep first-evidence order, and a late trust breach ("refund
    # of my deposit") outranks an early incidental match ("I can provide the deposit").
    objections.sort(key=lambda o: _OBJECTION_RANK.get(o.value, len(_OBJECTION_RANK)))

    return ReasonSignals(
        has_conversation=True,
        objections=objections,
        styles=styles,
        artists=artists,
        urgency=urgency,
        channel=channel,
        last_customer_message=cust_turns[-1]["text"],
        n_customer_turns=len(cust_turns),
    )
