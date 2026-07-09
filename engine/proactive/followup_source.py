"""followup_source — inbound reply + outcome capture -> the persistent memory loop.

IMPACT roadmap #2 (CustomerAcq-tlv.2). This is the path that ends "Groundhog Day":
after an outreach goes out, the customer's REAL response (an email reply, a Twilio
inbound SMS, an IG DM) — or their silence — is captured as durable, structured
state the NEXT run reasons over:

* the verbatim customer turn is APPENDED to ``lead_conversations``
  (:func:`studio.conversations.append_turn` — never replacing history), which is
  exactly what the psych analyst (:func:`studio.psych_profile.analyze_customer`)
  reads through the message-source adapter — so the next run's profile is grounded
  in the real last interaction with no extra wiring;
* a structured OUTCOME memory is written to the ``memories`` table
  (``subject_type='customer'``, ``metadata.kind='outcome'``, outcome one of
  ``replied | booked | objected:<type> | no_response``, verbatim reply kept) —
  replacing the old write-mostly "staged outreach" receipts as the loop's feedback
  signal. ``lookup_lead`` already surfaces these rows as ``facts['memories']``, and
  the Dossier's last-outreach/outcome block cites them.

HONESTY GATES (mirror the whole spine):
* An inbound whose sender cannot be resolved to a REAL customer row writes NOTHING —
  we never attribute a reply to a guessed customer.
* ``no_response`` records the silence WITHOUT inventing a conversation turn.
* Outcome classification is deterministic and grounded: ``booked`` only on an
  explicit booking phrase in the customer's own words; ``objected:<type>`` only when
  :func:`studio.reason_history.extract_signals` finds a real objection span; anything
  else is honestly just ``replied``. Nothing here sends.
* Webhook redelivery is idempotent end-to-end (turn dedupe + memory content-hash).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from studio.conversations import SPEAKER_CUSTOMER, append_turn
from studio.reason_history import (
    OBJECTION_PAYMENT,
    OBJECTION_PRICE,
    OBJECTION_TIMING,
    OBJECTION_TRUST,
    OBJECTION_UNCERTAINTY,
)

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

# The canonical outcome vocabulary (roadmap #2). ``objected`` is emitted as
# ``objected:<type>`` with the reason_history objection type (price/timing/trust/
# uncertainty/payment) so angle rotation (#3) can key on it directly.
OUTCOME_REPLIED = "replied"
OUTCOME_BOOKED = "booked"
OUTCOME_NO_RESPONSE = "no_response"
OUTCOME_OBJECTED_PREFIX = "objected:"

# Memory metadata kinds: ``outreach`` = we sent/staged something (written by the run
# loop); ``outcome`` = what actually happened after (written here).
OUTCOME_KIND = "outcome"
OUTREACH_KIND = "outreach"

# --------------------------------------------------------------------------- #
# SINGLE-REPLY outcome classification (qa1 rework). The prior version raw-reused
# ``reason_history.extract_signals`` (transcript-tuned) and matched booking phrases as
# UNANCHORED substrings — so "i'm in" fired inside "interested", "book it" fired inside
# "don't book it", "i booked" counted a booking with ANOTHER studio as our conversion,
# and a price INQUIRY read as an objection. That wrote FALSE durable memories that
# routed the warmest leads permanently off outreach. This classifier is purpose-built
# for a single inbound reply: Unicode-normalized, word-boundary matched, negation- and
# inquiry-gated, acceptance-not-interest. It errs toward ``replied`` (a human still sees
# the reply) over a confident wrong label — honest gaps beat a poisoned memory.
# --------------------------------------------------------------------------- #

# BOOKING ACCEPTANCE — a first-person commitment to book WITH US, in the customer's own
# words. Bare "i'm in" / "in" are EXCLUDED (they fire on "interested"/"I'm in Dallas").
# The ambiguous PAST-TENSE narratives "i booked" / "just booked" / "booked in" are also
# EXCLUDED (round-3 adversarial): they fire on "i booked my honeymoon", "booked in for
# jury duty", "just booked the gym" — an UNBOUNDED class a phrase-matcher can't enumerate.
# A real conversion uses an imperative directed at us ("book me in") or a deposit signal;
# the past-tense narrative is left to the LLM judge / ``replied`` (safe under-booking).
_BOOKING_PHRASES: tuple[str, ...] = (
    "book me in", "book me", "ready to book", "i'd like to book", "id like to book",
    "i want to book", "want to book", "let's book", "lets book", "book it",
    "sign me up", "count me in", "pencil me in", "lock it in", "lock me in",
    "see you then", "deposit sent", "deposit paid", "paid the deposit",
    "sent the deposit", "confirm my appointment", "appointment confirmed",
)
# COMPREHENSIVE negation lexicon — every common contraction + modal negative + bare
# negator, so "wouldn't/couldn't/shouldn't book" (qa1 round-2) are caught, not just
# "don't/can't". Negation is scoped to the CLAUSE the phrase sits in (see
# ``_clause_negated``) so a positive idiom in a NEIGHBOURING clause ("I can't wait,
# book me in") never cancels a real booking.
_NEGATORS: frozenset[str] = frozenset({
    "not", "no", "never", "none", "hardly", "barely", "without", "nor", "nope",
    "cannot", "cant", "can't", "dont", "don't", "doesnt", "doesn't", "didnt",
    "didn't", "wont", "won't", "wouldnt", "wouldn't", "couldnt", "couldn't",
    "shouldnt", "shouldn't", "isnt", "isn't", "arent", "aren't", "wasnt", "wasn't",
    "werent", "weren't", "aint", "ain't", "unlikely",
})
# Positive / FILLER idioms that CONTAIN a negator token but are NOT negations ("can't
# wait" is excitement; "not gonna lie" is a filler). Their negator is neutralized so it
# cannot cancel a following booking or suppress a real objection (round-3 adversarial).
_POSITIVE_IDIOMS: tuple[str, ...] = (
    "can't wait", "cant wait", "couldn't be happier", "couldnt be happier",
    "can't say no", "cant say no", "no problem", "no doubt", "no worries",
    "why not", "not gonna lie", "not going to lie", "cant lie", "can't lie",
    "no way i'm missing", "no way im missing", "wouldn't you know",
    "wouldnt you know", "no lie", "not gonna hold you",
)
# The booking is NOT ours when the reply says it happened elsewhere, or is a
# schedule-is-full ("booked up with work") statement — not a conversion for us.
_BOOKED_ELSEWHERE: tuple[str, ...] = (
    "another studio", "another artist", "another shop", "different studio",
    "different artist", "someone else", "somewhere else", "with another", "elsewhere",
)
_BOOKED_BUSY: tuple[str, ...] = (
    "with work", "all month", "booked up", "booked out", "booked solid",
    "with meetings", "so busy", "swamped", "full up", "double shift",
    "double shifts", "jury duty", "smear test", "for its mot", "for an mot",
)
# NON-STUDIO booking objects — "i booked a flight / my dentist / a table" is a booking
# of something ELSE, not a conversion for us. Bundled with the "elsewhere" suppressors so
# the ambiguous past-tense "i booked" / "just booked" never counts one of these as ours.
_BOOKED_OTHER_THING: tuple[str, ...] = (
    "a flight", "flights", "a hotel", "the hotel", "a room", "a table", "a trip",
    "a holiday", "a vacation", "an uber", "a cab", "a taxi", "a ticket", "tickets",
    "a restaurant", "the restaurant", "my dentist", "the dentist", "a doctor",
    "the doctor", "a car", "time off", "a meeting", "an appointment with",
)
# Question detection (principled, replaces a brittle opener list): a reply is an INQUIRY
# — a buying/engagement signal, not a booking or an objection — when it ends with "?" OR
# opens with a wh-word, OR opens with a question auxiliary IMMEDIATELY followed by a
# subject pronoun ("can i", "do you", "is it", "would you"). The aux+pronoun requirement
# keeps affirmations that start with an auxiliary ("will do, book me in"; "would love to
# book") from being misread as questions.
_WH_OPENER = re.compile(r"^(what|when|where|which|who|why|how)\b")
_AUX_QUESTION = re.compile(
    r"^(can|could|would|will|do|does|did|is|are|am|was|were|should|shall|have|has)\s+"
    r"(i|we|you|u|it|they|there|that|he|she|this|these|ya)\b"
)

# OBJECTION (genuine resistance / blocker) phrases, curated for SINGLE replies — a real
# hesitation, never an inquiry or a commitment. Ordered most-specific-first (payment >
# price > timing > trust > uncertainty). PRINCIPLE (qa1 round-2): every phrase here must
# read as resistance ON ITS OWN. Bare ambiguous tokens are EXCLUDED because they fire in
# POSITIVE context: "afford" (dropped — "can afford" is positive; only the explicit
# "can't/cannot/couldn't afford" negative forms stay), "first tattoo" (dropped — a
# first-timer can be excited, not only nervous), plus the earlier drops
# ("maybe"/"might"/"budget"/"deposit"/"thinking"). "expensive"/"pricey" stay but are
# CLAUSE-NEGATION-guarded so "not expensive" never fires.
_REPLY_OBJECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (OBJECTION_PAYMENT, (
        "payment plan", "pay it off", "installment", "instalment", "split the cost",
        "split it up", "afterpay", "klarna", "financing", "pay later",
        "pay in install", "spread the cost",
    )),
    (OBJECTION_PRICE, (
        "too expensive", "too much", "too costly", "too pricey", "a bit much",
        "bit pricey", "pricey", "expensive", "out of my range", "out of my budget",
        "out of budget", "out of my price range", "price range", "outta my",
        "cannot afford", "can't afford", "cant afford", "couldn't afford",
        "couldnt afford", "on a budget", "tight budget", "short on budget", "steep",
        "save up", "cheaper", "costs too much", "found someone cheaper",
    )),
    (OBJECTION_TIMING, (
        "maybe later", "not right now", "some other time", "down the road",
        "hold off", "put it off", "next month", "next year", "when things settle",
        "circle back", "reach out later", "later in the year", "after the holidays",
        "not this month", "too busy right now",
    )),
    (OBJECTION_TRUST, (
        "nervous", "scared", "worried", "hesitant", "second thoughts",
        "is it safe", "is it clean", "does it hurt", "will it hurt", "hygiene",
        "afraid",
    )),
    (OBJECTION_UNCERTAINTY, (
        "not sure", "unsure", "still deciding", "still thinking", "on the fence",
        "haven't decided", "havent decided", "don't know if", "dont know if",
        "not certain", "undecided", "need to think", "have to think",
    )),
)


def _dsn(dsn: str | None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def _normalize_reply(text: str) -> str:
    """Lowercase + fold the Unicode punctuation real phones emit (iOS smart quotes
    U+2019/U+2018 -> ``'``, curly double-quotes -> ``"``, en/em dashes -> ``-``) and
    collapse whitespace — so ``I'd like to book`` (curly) matches ``i'd like to book``.
    Without this, iOS's default apostrophe silently dropped real bookings."""
    t = (text or "").lower()
    for src, dst in (("’", "'"), ("‘", "'"), ("“", '"'),
                     ("”", '"'), ("–", "-"), ("—", "-")):
        t = t.replace(src, dst)
    return re.sub(r"\s+", " ", t).strip()


# Clause boundaries: sentence punctuation + coordinating/contrastive conjunctions.
# Negation does not cross these, so "I can't wait, book me in" keeps "book me in"
# un-negated ("can't wait" is in a different clause).
_CLAUSE_SPLIT = re.compile(
    r"[.;!?,]|\bbut\b|\band\b|\bso\b|\bthen\b|\bthough\b|\bhowever\b|\byet\b"
)


def _clauses(norm: str) -> list[str]:
    return [c.strip() for c in _CLAUSE_SPLIT.split(norm) if c.strip()]


def _has_phrase(text: str, phrase: str) -> bool:
    """Word-boundary phrase match — so ``see you on`` does NOT fire inside ``see you
    online`` and ``i'm in`` does NOT fire inside ``interested`` (the qa1 false-BOOKED
    bug). ``\\b`` around an apostrophe still anchors ("can't afford")."""
    return re.search(r"\b" + re.escape(phrase) + r"\b", text) is not None


def _clause_negated(clause: str, phrase: str) -> bool:
    """True when ``phrase`` is negated within its own clause — a negator token remains in
    the clause once the phrase and any positive/filler idioms are removed. Removing the
    phrase first means a phrase that CONTAINS a negator ("not sure", "can't afford") does
    not self-negate, while catching negation BEFORE ("wouldn't book it") AND AFTER ("a
    payment plan isn't needed") the phrase (round-3 adversarial). Clause-scoping stops a
    positive idiom in a neighbouring clause from cancelling a real booking."""
    scrubbed = clause
    for idiom in _POSITIVE_IDIOMS:
        scrubbed = scrubbed.replace(idiom, " ")
    # Remove the phrase occurrences so their own negator tokens are not counted.
    scrubbed = re.sub(r"\b" + re.escape(phrase) + r"\b", " ", scrubbed)
    return any(tok.strip(".,!?;'\"") in _NEGATORS for tok in scrubbed.split())


# Informal / indirect question markers a wh/aux+pronoun opener misses ("u free to...",
# "you got space...", "is now a good time...", "...or nah") — still inquiries.
_INFORMAL_Q_OPENERS: tuple[str, ...] = (
    "you got", "u got", "you free", "u free", "you around", "u around",
    "is now", "any chance", "wanna know", "any openings", "any availability",
    "got space", "got any", "hows", "how's", "whens", "when's", "you have time",
    "you have any",
)
_INFORMAL_Q_TAILS: tuple[str, ...] = ("or nah", "or not", "or what", "or no")
# "is booking gonna be pricey", "will it be expensive" — a leading auxiliary + a future
# "gonna"/"going to" construction is a cost/availability QUESTION, not a statement.
_AUX_FUTURE_Q = re.compile(
    r"^(is|are|was|were|will|would|can|could|do|does|did|should|shall)\b.*"
    r"\b(gonna|going to)\b"
)


def _is_inquiry(norm: str) -> bool:
    """A reply that is a question is a buying / engagement signal, not a booking or an
    objection on its own. Detected by a trailing ``?``; a leading wh-word; a leading
    question-auxiliary+pronoun ("can i book it right now" — the aux+pronoun rule avoids
    misreading affirmations "will do"/"would love to book" as questions); an informal
    indirect opener ("u free to...", "is now a good time..."); or an informal question
    TAIL ("...friday or nah")."""
    if norm.endswith("?"):
        return True
    if _WH_OPENER.match(norm) or _AUX_QUESTION.match(norm) or _AUX_FUTURE_Q.match(norm):
        return True
    if any(norm.startswith(op) for op in _INFORMAL_Q_OPENERS):
        return True
    return any(norm.endswith(t) for t in _INFORMAL_Q_TAILS)


def _is_booking_acceptance(norm: str) -> bool:
    """A genuine first-person booking commitment to US: a booking phrase that appears in
    some clause NOT negated within that clause, and the reply is not a booking-elsewhere
    / non-studio / schedule-is-full statement. Clause-scoped negation catches every
    contraction ("wouldn't/couldn't/shouldn't book") without falsely negating "I can't
    wait, book"."""
    if any(_has_phrase(norm, p)
           for p in _BOOKED_ELSEWHERE + _BOOKED_BUSY + _BOOKED_OTHER_THING):
        return False
    clauses = _clauses(norm)
    for p in _BOOKING_PHRASES:
        for clause in clauses:
            if _has_phrase(clause, p) and not _clause_negated(clause, p):
                return True
    return False


def _reply_objection(norm: str) -> str | None:
    """The objection TYPE a single reply genuinely voices, or None. A resistance phrase
    counts only when it appears in a clause where it is NOT negated ("not expensive" is
    not a price objection), most-specific type first."""
    clauses = _clauses(norm)
    for otype, phrases in _REPLY_OBJECTIONS:
        for p in phrases:
            for clause in clauses:
                if _has_phrase(clause, p) and not _clause_negated(clause, p):
                    return otype
    return None


@dataclass(frozen=True)
class InboundCapture:
    """The durable result of one captured inbound signal: which real customer it was,
    the classified outcome, and the ids of the two rows the loop now remembers by."""

    customer_id: str
    outcome: str
    channel: str
    conversation_id: str
    memory_id: str
    turn_appended: bool  # False on an exact webhook redelivery (idempotent)


def classify_outcome(text: str) -> str:
    """Deterministically classify one verbatim customer reply into the outcome
    vocabulary — ``booked | objected:<type> | replied`` — for a SINGLE inbound reply.

    Principled (qa1 round-2): clause-scoped negation (catches every contraction
    "wouldn't/couldn't/shouldn't book", never falsely negating "I can't wait, book me
    in"), leading+trailing interrogative detection, and objection phrases tightened to
    genuine resistance so a POSITIVE context ("I can afford it, book me in";
    "not expensive, let's book"; "first tattoo, excited, book me in") is NOT read as an
    objection. It errs toward ``replied`` over a confident wrong label — a human still
    sees the reply, and a poisoned memory (false ``booked`` routes a warm lead off
    outreach) is silent and permanent.

    Order: an INQUIRY (a question — "How much is the deposit?", "Can I book it right
    now") is a buying signal, not a booking or an objection -> ``replied``. Else a
    genuine RESISTANCE blocker wins ("book but can't afford" -> ``objected:price``, the
    signal angle-rotation needs). Else an un-negated, not-elsewhere booking acceptance
    -> ``booked``. Else ``replied``."""
    clean = (text or "").strip()
    if not clean:
        raise ValueError("reply text is empty")
    norm = _normalize_reply(clean)

    # 1) A question is an inquiry (buying/engagement signal) — never a booking or an
    #    objection on its own. Gate FIRST so a price/timing question does not read as
    #    resistance and a "Can I book ...?" does not read as a conversion.
    if _is_inquiry(norm):
        return OUTCOME_REPLIED

    # 2) Genuine, non-negated resistance overrides an accompanying booking word
    #    ("book but can't afford"). Curated blockers only; positive/negated context
    #    ("can afford", "not expensive") does not fire here.
    blocker = _reply_objection(norm)
    if blocker is not None:
        return f"{OUTCOME_OBJECTED_PREFIX}{blocker}"

    # 3) A genuine booking acceptance (not negated in its clause, not booked-elsewhere).
    if _is_booking_acceptance(norm):
        return OUTCOME_BOOKED

    return OUTCOME_REPLIED


# --------------------------------------------------------------------------- #
# LLM-JUDGE fallback (operator-authorized escalation). The deterministic classifier
# above is the SAFE, hermetic FLOOR — it never false-books on a BOUNDED class. But a
# single reply can book an UNBOUNDED "something else" ("i booked my honeymoon", "booked
# in for jury duty", "went with a place closer to home") that no phrase list can
# enumerate. When an LLM is available, a temp-0 judge adjudicates the reply with real
# semantic understanding; on ANY failure the deterministic floor stands. Mirrors the
# psych_profile deterministic-floor + optional-LLM-through-a-gate pattern. Off by default
# in keyless/hermetic runs (tests) — those exercise the deterministic floor.
# --------------------------------------------------------------------------- #
_VALID_OBJECTION_TYPES: frozenset[str] = frozenset(
    {OBJECTION_PRICE, OBJECTION_PAYMENT, OBJECTION_TIMING, OBJECTION_TRUST,
     OBJECTION_UNCERTAINTY}
)


def _reply_judge_schema():
    from pydantic import BaseModel, Field

    class ReplyJudgeOut(BaseModel):
        """The structured verdict the reply-judge cell returns."""

        outcome: str = Field(
            description="one of: booked | objected:price | objected:payment | "
            "objected:timing | objected:trust | objected:uncertainty | replied"
        )
        confidence: float = 0.0
        reason: str = ""

    return ReplyJudgeOut


# Bound at import so the cell schema is a stable class (pydantic model identity).
ReplyJudgeOut = _reply_judge_schema()


def _reply_judge_enabled() -> bool:
    """Whether to consult the LLM judge. Honors ``$SCALERS_INBOUND_LLM`` (1/0); else auto
    (on iff an Anthropic key is present). Keyless CI / tests -> off (deterministic floor)."""
    override = os.environ.get("SCALERS_INBOUND_LLM")
    if override is not None:
        return override.strip().lower() in ("1", "true", "yes", "on")
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _valid_outcome(label: str | None) -> bool:
    """A judge label is usable only if it is exactly in the outcome vocabulary."""
    if not label:
        return False
    if label in (OUTCOME_BOOKED, OUTCOME_REPLIED, OUTCOME_NO_RESPONSE):
        return True
    if label.startswith(OUTCOME_OBJECTED_PREFIX):
        return label[len(OUTCOME_OBJECTED_PREFIX):] in _VALID_OBJECTION_TYPES
    return False


def _build_reply_judge_cell():
    from cells.base import Cell
    from cells.validators import ValidatorBank

    instructions = (
        "You classify ONE inbound reply a customer sent a TATTOO STUDIO after the studio's "
        "outreach. Output EXACTLY one outcome label:\n"
        "  booked | objected:price | objected:payment | objected:timing | "
        "objected:trust | objected:uncertainty | replied\n\n"
        "DEFINITIONS:\n"
        "- booked = the customer commits to booking an appointment WITH THIS studio (a "
        "clear yes / 'book me in' / deposit sent). It is NOT booked if they booked "
        "something ELSE (a flight, the gym, jury duty, ANOTHER studio or artist), if they "
        "are ASKING a question, if they are only interested/excited without committing, or "
        "if the booking is negated ('wouldn't book').\n"
        "- objected:<type> = a genuine blocker they STATE (not ask): price (too expensive / "
        "can't afford), payment (need a plan), timing (maybe later / not now), trust "
        "(nervous / scared / does it hurt), uncertainty (not sure / still deciding).\n"
        "- replied = everything else: questions/inquiries (including price questions — a "
        "buying signal, not an objection), ambiguous, neutral, positive-but-no-commitment.\n\n"
        "SAFETY (critical): a false 'booked' PERMANENTLY removes a warm lead from outreach "
        "via a durable memory. When genuinely uncertain, choose 'replied'. If a booking "
        "word AND a real blocker both appear, prefer the objection. NEVER invent a "
        "commitment or a blocker the words do not show."
    )
    return Cell(
        name="inbound_reply_judge",
        schema=ReplyJudgeOut,
        instructions=instructions,
        validators=ValidatorBank(validators=()),
    )


def classify_reply(text: str, *, use_llm: bool | None = None) -> str:
    """Classify one inbound reply — the deterministic floor, refined by the LLM judge
    when available. ``classify_outcome`` (pure/deterministic) is always the fallback, so
    a keyless run, a judge error, or an off-vocabulary judge label all resolve to the
    safe deterministic label. Raises ``ValueError`` on empty text (before any judge)."""
    deterministic = classify_outcome(text)  # raises on empty; the safe floor
    do_llm = _reply_judge_enabled() if use_llm is None else use_llm
    if not do_llm:
        return deterministic
    try:
        cell = _build_reply_judge_cell()
        out = cell.run_sync(f"CUSTOMER REPLY:\n{text.strip()}")
        label = (getattr(out, "outcome", "") or "").strip().lower()
        if _valid_outcome(label):
            # The judge is the more capable classifier for the ambiguous/semantic cases
            # the phrase-matcher cannot close; a valid label wins. Its own safety prompt
            # biases it toward 'replied' and away from false 'booked'.
            return label
    except Exception:
        pass  # any cell/model failure -> the deterministic floor stands (safe)
    return deterministic


def _connect(dsn: str | None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), autocommit=True, row_factory=dict_row)


def resolve_customer(
    tenant_id: str,
    *,
    email: str | None = None,
    phone: str | None = None,
    ig_handle: str | None = None,
    dsn: str | None = None,
) -> str | None:
    """Resolve an inbound sender to a REAL ``customers`` row id, or ``None``.

    Matches per identifier: email case-insensitive; IG handle case-insensitive and
    ``@``-tolerant; phone by its LAST 10 digits (so Twilio's E.164 ``+15551230001``
    matches a human-stored ``(555) 123-0001`` — qa1: exact string equality dropped
    every SMS reply). ``None`` means "we do not know this sender" — the honest answer;
    the caller writes nothing.

    A DB error is NOT swallowed (qa1): it PROPAGATES so the webhook returns 5xx and the
    provider retries — the old blanket ``except: return None`` turned a transient
    outage into a 404 the provider marked delivered, permanently losing the signal."""
    clauses: list[str] = []
    params: list[Any] = []
    if email and email.strip():
        clauses.append("lower(email) = lower(%s)")
        params.append(email.strip())
    if phone and phone.strip():
        digits = re.sub(r"\D", "", phone)
        if digits:
            # Compare the last 10 digits on both sides (NANP), formatting-agnostic.
            clauses.append(
                "right(regexp_replace(phone, '[^0-9]', '', 'g'), 10) = right(%s, 10)"
            )
            params.append(digits)
    if ig_handle and ig_handle.strip():
        clauses.append("lower(ltrim(ig_handle, '@')) = lower(%s)")
        params.append(ig_handle.strip().lstrip("@"))
    if not clauses:
        return None
    # No try/except: a real DB error must surface as 5xx (provider retries), never a
    # silent 404. A missing customers table IS a real misconfiguration, not "unknown".
    with _connect(dsn) as conn:
        row = conn.execute(
            "SELECT id FROM customers WHERE tenant_id = %s AND ("
            + " OR ".join(clauses) + ") LIMIT 1",
            [tenant_id, *params],
        ).fetchone()
    return row["id"] if row else None


def _customer_exists(tenant_id: str, customer_id: str, *, dsn: str | None = None) -> bool:
    """Whether ``customer_id`` is a REAL row for this tenant. Gates the caller-supplied
    id path so a webhook cannot phantom-write a turn + memory for a customer that does
    not exist (qa1: the 'never attribute to a guessed customer' gate only protected the
    email/phone/ig lookup path). A DB error propagates (5xx, not a silent skip)."""
    with _connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM customers WHERE tenant_id = %s AND id = %s LIMIT 1",
            (tenant_id, customer_id),
        ).fetchone()
    return row is not None


def _memory_store(memory_store: Any | None, dsn: str | None):
    if memory_store is not None:
        return memory_store
    from memory import MemoryStore

    return MemoryStore(dsn=_dsn(dsn))


def capture_inbound(
    tenant_id: str,
    *,
    text: str,
    channel: str,
    customer_id: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    ig_handle: str | None = None,
    source: str | None = None,
    run_id: str | None = None,
    use_llm: bool | None = None,
    memory_store: Any | None = None,
    dsn: str | None = None,
) -> InboundCapture | None:
    """Capture ONE inbound customer signal end-to-end: append the verbatim turn to
    ``lead_conversations`` AND write the structured outcome memory.

    The sender is ``customer_id`` when the webhook already knows it (VERIFIED to be a
    real row for this tenant — qa1: an unverified caller id must not phantom-write),
    else resolved from ``email`` / ``phone`` / ``ig_handle`` against the real
    ``customers`` table. Returns ``None`` (and writes NOTHING) when the sender does not
    resolve OR the supplied id does not exist — a reply is never attributed to a guessed
    customer. Raises ``ValueError`` on empty text/channel (endpoint maps to 422); a DB
    error propagates (endpoint maps to 5xx so the provider retries). Idempotent on
    redelivery: the turn dedupes against the whole thread and the memory upserts on its
    content hash."""
    # Classify via the deterministic floor + the LLM judge when available (the judge
    # catches the unbounded "booked something else / elsewhere" cases a phrase-matcher
    # cannot). Raises on empty text before any write.
    outcome = classify_reply(text, use_llm=use_llm)
    clean = text.strip()
    if not (channel or "").strip():
        raise ValueError("channel is required")
    if customer_id:
        # A caller-supplied id must be a REAL customer of this tenant before we write.
        if not _customer_exists(tenant_id, customer_id, dsn=dsn):
            return None
    else:
        customer_id = resolve_customer(
            tenant_id, email=email, phone=phone, ig_handle=ig_handle, dsn=dsn
        )
        if customer_id is None:
            return None

    src = source or f"inbound-{channel}"
    conversation_id, turn_appended = append_turn(
        tenant_id, customer_id, clean,
        speaker=SPEAKER_CUSTOMER, channel=channel, source=src, dsn=dsn,
    )

    store = _memory_store(memory_store, dsn)
    store.ensure_schema()
    metadata: dict[str, Any] = {
        "kind": OUTCOME_KIND, "outcome": outcome, "channel": channel,
        "verbatim": clean, "source": src,
    }
    if run_id:
        metadata["run_id"] = run_id
    memory_id = store.write(
        tenant_id=tenant_id, subject_type="customer", subject_id=customer_id,
        text=(f"Inbound {channel} reply -> outcome: {outcome}. "
              f'Customer said: "{clean}"'),
        metadata=metadata,
    )
    return InboundCapture(
        customer_id=customer_id, outcome=outcome, channel=channel,
        conversation_id=conversation_id, memory_id=memory_id,
        turn_appended=turn_appended,
    )


def record_no_response(
    tenant_id: str,
    customer_id: str,
    *,
    channel: str,
    run_id: str | None = None,
    action_id: str | None = None,
    memory_store: Any | None = None,
    dsn: str | None = None,
) -> str | None:
    """Record that an outreach got NO reply — an outcome memory only, NO conversation
    turn (the customer said nothing; we never fabricate a turn). Intended caller: the
    scheduler's follow-up sweep (fr1.1) after its no-reply window elapses. Idempotent
    per (customer, run) via the memory content hash.

    BOOKED-GUARD (qa1): a customer whose latest captured outcome is ``booked`` has
    CONVERTED — the sweep must not overwrite that with ``no_response`` and flip a booked
    lead back onto the cold-outreach path. Returns ``None`` (writes nothing) in that
    case; else the new memory's id."""
    store = _memory_store(memory_store, dsn)
    store.ensure_schema()
    existing = [
        {"text": m.text, "metadata": m.metadata}
        for m in store.list_for_subject(
            tenant_id=tenant_id, subject_type="customer", subject_id=customer_id
        )
    ]
    if is_booked(existing):
        return None  # already converted — never flip a booked lead back to cold
    metadata: dict[str, Any] = {
        "kind": OUTCOME_KIND, "outcome": OUTCOME_NO_RESPONSE, "channel": channel,
    }
    if run_id:
        metadata["run_id"] = run_id
    if action_id:
        metadata["action_id"] = action_id
    run_bit = f" (run {run_id})" if run_id else ""
    return store.write(
        tenant_id=tenant_id, subject_type="customer", subject_id=customer_id,
        text=(f"No response to the last {channel} outreach{run_bit} — "
              "no reply captured within the follow-up window."),
        metadata=metadata,
    )


def _meta(memory: Any) -> dict[str, Any]:
    """Metadata of one memory row, tolerating the ``facts['memories']`` dict shape
    AND the :class:`memory.store.Memory` dataclass."""
    if isinstance(memory, dict):
        return memory.get("metadata") or {}
    return getattr(memory, "metadata", None) or {}


def latest_outcome(memories: list[Any] | None) -> dict[str, Any] | None:
    """The metadata of the NEWEST outcome memory in a lead's ``facts['memories']``
    list (which ``lookup_lead`` returns newest-first), or ``None`` when the loop has
    no captured outcome yet — an honest blank, never a guessed history."""
    for m in memories or []:
        meta = _meta(m)
        if meta.get("kind") == OUTCOME_KIND and meta.get("outcome"):
            return dict(meta)
    return None


def is_booked(memories: list[Any] | None) -> bool:
    """True when the lead's most recent captured outcome is ``booked`` — the signal
    that routes them OFF the cold-outreach path (they converted; the next touch is
    aftercare/loyalty, not another cold pitch)."""
    outcome = latest_outcome(memories)
    return bool(outcome and outcome.get("outcome") == OUTCOME_BOOKED)
