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
# words. Bare "i'm in" / "in" are deliberately EXCLUDED (they fire on "interested",
# "I'm in Dallas"); an ambiguous acceptance is safer left as ``replied``.
_BOOKING_PHRASES: tuple[str, ...] = (
    "book me in", "book me", "ready to book", "i'd like to book", "id like to book",
    "i want to book", "want to book", "let's book", "lets book", "book it",
    "i booked", "just booked", "booked in", "sign me up", "count me in",
    "see you then", "see you on", "deposit sent", "deposit paid",
    "paid the deposit", "sent the deposit", "confirm my appointment",
    "appointment confirmed", "confirmed for",
)
# Negators that, immediately before a booking phrase, cancel it ("don't book it yet").
_NEGATORS: tuple[str, ...] = (
    "not", "dont", "don't", "cant", "can't", "cannot", "wont", "won't",
    "never", "no", "isnt", "isn't", "wasnt", "wasn't",
)
# The booking is NOT ours when the reply says it happened elsewhere, or is a
# schedule-is-full ("booked up with work") statement — not a conversion for us.
_BOOKED_ELSEWHERE: tuple[str, ...] = (
    "another studio", "another artist", "another shop", "different studio",
    "different artist", "someone else", "somewhere else", "with another", "elsewhere",
)
_BOOKED_BUSY: tuple[str, ...] = (
    "with work", "all month", "booked up", "booked solid", "with meetings",
    "so busy", "swamped", "full up",
)

# OBJECTION (genuine resistance / blocker) phrases, curated for SINGLE replies — a real
# hesitation, never an inquiry or a commitment. Ordered most-specific-first (payment >
# price > timing > trust > uncertainty), aligned to the reason_history taxonomy. Bare
# "maybe"/"might"/"budget"/"deposit"/"thinking" are EXCLUDED (they over-fire on single
# replies: "Maybe Friday works?", "How much is the deposit?", "I'll pay the deposit").
_REPLY_OBJECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (OBJECTION_PAYMENT, (
        "payment plan", "pay it off", "installment", "instalment", "split the cost",
        "split it", "afterpay", "klarna", "financing", "pay later", "pay in install",
        "can i pay it off", "spread the cost",
    )),
    (OBJECTION_PRICE, (
        "too expensive", "too much", "a bit much", "bit pricey", "pricey",
        "out of my range", "out of my budget", "cannot afford", "can't afford",
        "cant afford", "afford", "on a budget", "tight budget", "short on budget",
        "steep", "save up", "too costly", "expensive", "cheaper",
    )),
    (OBJECTION_TIMING, (
        "maybe later", "not right now", "some other time", "down the road",
        "hold off", "put it off", "next month", "next year", "when things settle",
        "circle back", "reach out later", "later in the year", "after the holidays",
    )),
    (OBJECTION_TRUST, (
        "first tattoo", "nervous", "scared", "worried", "hesitant",
        "second thoughts", "is it safe", "is it clean", "hygiene",
    )),
    (OBJECTION_UNCERTAINTY, (
        "not sure", "unsure", "still deciding", "still thinking", "on the fence",
        "haven't decided", "havent decided", "don't know if", "dont know if",
        "not certain", "undecided", "need to think",
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


def _has_phrase(norm: str, phrase: str) -> bool:
    """Word-boundary phrase match on already-normalized text — so ``book it`` does NOT
    fire inside ``don't book it``'s… wait, that IS a match; the negation guard handles
    that. What this stops is ``see you on`` firing inside ``see you online`` and
    ``i'm in`` firing inside ``interested`` (the qa1 false-BOOKED bug)."""
    return re.search(r"\b" + re.escape(phrase) + r"\b", norm) is not None


def _is_negated(norm: str, phrase: str) -> bool:
    """True when a booking phrase is immediately preceded (within ~3 tokens) by a
    negator — ``don't book it yet`` is not a booking."""
    for m in re.finditer(r"\b" + re.escape(phrase) + r"\b", norm):
        prefix_tokens = norm[: m.start()].split()
        if any(tok in _NEGATORS for tok in prefix_tokens[-3:]):
            return True
    return False


def _is_booking_acceptance(norm: str) -> bool:
    """A genuine first-person booking commitment to US — word-boundary matched, not
    negated, not a booking elsewhere, not a schedule-is-full statement."""
    if any(_has_phrase(norm, p) for p in _BOOKED_ELSEWHERE + _BOOKED_BUSY):
        return False
    for p in _BOOKING_PHRASES:
        if _has_phrase(norm, p) and not _is_negated(norm, p):
            return True
    return False


def _reply_objection(norm: str) -> str | None:
    """The objection TYPE a single reply genuinely voices, or None. Curated resistance
    phrases only (no inquiry/commitment words), most-specific-first."""
    for otype, phrases in _REPLY_OBJECTIONS:
        if any(_has_phrase(norm, p) for p in phrases):
            return otype
    return None


def _is_inquiry(norm: str) -> bool:
    """A reply that is a question is a buying/engagement signal, not a booking or an
    objection on its own ("Can I book right now?", "How much is the deposit?"). We treat
    a trailing ``?`` as the inquiry marker — conservative, and it kept the qa1 price/
    timing INQUIRIES from being recorded as objections."""
    return norm.endswith("?")


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

    Order (qa1-hardened): a genuine RESISTANCE blocker wins first, so
    "I want to book but I cannot afford it right now" is ``objected:price`` (the signal
    angle-rotation needs), NOT ``booked``. Otherwise a real, un-negated,
    not-elsewhere booking acceptance in a non-question reply is ``booked``. Otherwise
    ``replied``. Inquiries ("How much is the deposit?", "Can I book right now?") and
    commitments ("I'll pay the deposit tomorrow") fall to ``replied`` — a human still
    sees them; we never write a confident wrong label that poisons future runs."""
    clean = (text or "").strip()
    if not clean:
        raise ValueError("reply text is empty")
    norm = _normalize_reply(clean)

    # 1) Genuine resistance overrides an accompanying booking word ("book but can't
    #    afford"). Curated blockers only — inquiries/commitments do not fire here.
    blocker = _reply_objection(norm)
    if blocker is not None:
        return f"{OUTCOME_OBJECTED_PREFIX}{blocker}"

    # 2) A booking acceptance (not a question, not negated, not booked-elsewhere/busy).
    if not _is_inquiry(norm) and _is_booking_acceptance(norm):
        return OUTCOME_BOOKED

    return OUTCOME_REPLIED


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
    outcome = classify_outcome(text)  # raises on empty text before any write
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
