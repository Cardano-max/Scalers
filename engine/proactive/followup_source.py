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
from dataclasses import dataclass
from typing import Any

from studio.conversations import SPEAKER_CUSTOMER, append_turn
from studio.reason_history import extract_signals

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

# Explicit booking-confirmation phrases — the customer, in their own words, committing.
# Deliberately conservative: a phrase here must be an acceptance, not interest. Checked
# BEFORE objection extraction because a conversion is the stronger, more specific
# signal ("book me in, deposit sent" is booked, not a payment objection).
_BOOKING_PHRASES: tuple[str, ...] = (
    "book me", "i'd like to book", "id like to book", "i want to book",
    "want to book", "let's book", "lets book", "book it", "i booked", "just booked",
    "booked in", "sign me up", "count me in", "i'm in", "im in", "see you then",
    "see you on", "deposit sent", "deposit paid", "paid the deposit",
    "sent the deposit", "confirm my appointment", "appointment confirmed",
    "confirmed for",
)


def _dsn(dsn: str | None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


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
    vocabulary. Grounded: ``booked`` needs an explicit booking phrase in the
    customer's own words; ``objected:<type>`` needs a real objection span
    (:func:`~studio.reason_history.extract_signals`); otherwise ``replied`` —
    never an invented outcome."""
    clean = (text or "").strip()
    if not clean:
        raise ValueError("reply text is empty")
    low = clean.lower()
    if any(p in low for p in _BOOKING_PHRASES):
        return OUTCOME_BOOKED
    signals = extract_signals([{"speaker": SPEAKER_CUSTOMER, "text": clean}])
    if signals.objections:
        return f"{OUTCOME_OBJECTED_PREFIX}{signals.objections[0].value}"
    return OUTCOME_REPLIED


def resolve_customer(
    tenant_id: str,
    *,
    email: str | None = None,
    phone: str | None = None,
    ig_handle: str | None = None,
    dsn: str | None = None,
) -> str | None:
    """Resolve an inbound sender to a REAL ``customers`` row id, or ``None``.

    Matches are exact per identifier (email case-insensitive; IG handle
    case-insensitive and ``@``-tolerant). ``None`` means "we do not know this
    sender" — the honest answer; the caller must then write nothing."""
    import psycopg
    from psycopg.rows import dict_row

    clauses: list[str] = []
    params: list[Any] = []
    if email and email.strip():
        clauses.append("lower(email) = lower(%s)")
        params.append(email.strip())
    if phone and phone.strip():
        clauses.append("phone = %s")
        params.append(phone.strip())
    if ig_handle and ig_handle.strip():
        clauses.append("lower(ltrim(ig_handle, '@')) = lower(%s)")
        params.append(ig_handle.strip().lstrip("@"))
    if not clauses:
        return None
    try:
        with psycopg.connect(_dsn(dsn), autocommit=True, row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT id FROM customers WHERE tenant_id = %s AND ("
                + " OR ".join(clauses) + ") LIMIT 1",
                [tenant_id, *params],
            ).fetchone()
    except Exception:
        return None  # no customers table / DB hiccup -> honest unresolved, no write
    return row["id"] if row else None


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

    The sender is ``customer_id`` when the webhook already knows it, else resolved
    from ``email`` / ``phone`` / ``ig_handle`` against the real ``customers`` table.
    Returns ``None`` (and writes NOTHING) when the sender does not resolve — a reply
    is never attributed to a guessed customer. Raises on empty text. Idempotent on
    redelivery: the turn dedupes against the thread tail and the memory upserts on
    its content hash."""
    outcome = classify_outcome(text)  # raises on empty text before any write
    clean = text.strip()
    if not (channel or "").strip():
        raise ValueError("channel is required")
    if not customer_id:
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
) -> str:
    """Record that an outreach got NO reply — an outcome memory only, NO conversation
    turn (the customer said nothing; we never fabricate a turn). Intended caller: the
    scheduler's follow-up sweep (fr1.1) after its no-reply window elapses. Idempotent
    per (customer, run) via the memory content hash."""
    store = _memory_store(memory_store, dsn)
    store.ensure_schema()
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
