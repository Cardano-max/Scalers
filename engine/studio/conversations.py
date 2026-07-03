"""Per-lead CONVERSATION store — the durable home for a warm lead's prior message
history (the signal the psychology analyst + objection branching reason over).

A tattoo studio's warmest data is the back-and-forth a customer already had with the
shop ("I like it but maybe later, short on budget right now"). Today an uploaded CSV
lands only in ``customers``; there was nowhere to keep the CONVERSATION. This is that
home: a tenant-scoped ``lead_conversations`` table keyed ``(tenant_id, customer_id)``
holding the ordered turns as JSONB, plus the campaign message that opened the thread.

It is deliberately a SIDE table (no hard FK to ``customers``, which is provisioned by
infra, not this repo) so it is reproducible on a fresh DB and a no-op on the live one —
the same ``CREATE TABLE IF NOT EXISTS`` convention as :mod:`memory.store` and
:mod:`studio.documents`. Sync psycopg, offloaded via ``asyncio.to_thread`` by callers.

HONESTY GATE: :func:`get_conversation` returns ``None`` for a lead with no stored
thread — never a fabricated conversation. Every turn read back is a real stored turn.
The adapters in :mod:`studio.adapters.message_source` are the seam that will later swap
this DB source for Stribe / Mini-App CRM without changing any reasoning node.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

# The canonical speakers on a stored turn. ``customer`` = inbound (the lead), ``studio``
# = outbound (the shop). Kept tiny + explicit so the ABSA extractor can trust direction.
SPEAKER_CUSTOMER = "customer"
SPEAKER_STUDIO = "studio"
_VALID_SPEAKERS = frozenset({SPEAKER_CUSTOMER, SPEAKER_STUDIO})


def _dsn(dsn: str | None = None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def _connect(dsn: str | None = None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), autocommit=True, row_factory=dict_row)


def ensure_schema(dsn: str | None = None) -> None:
    """Idempotently create ``lead_conversations`` (a no-op on the live cluster).

    One row per (tenant, customer): the ordered ``turns`` JSONB, the opening campaign
    message (``campaign_message``), the ``channel`` and ``source`` it came from."""
    with _connect(dsn) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lead_conversations (
                id               TEXT PRIMARY KEY,
                tenant_id        TEXT NOT NULL,
                customer_id      TEXT NOT NULL,
                channel          TEXT,
                source           TEXT NOT NULL DEFAULT 'upload',
                campaign_message TEXT,
                turns            JSONB NOT NULL DEFAULT '[]'::jsonb,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE UNIQUE INDEX IF NOT EXISTS lead_conversations_natural_key
                ON lead_conversations (tenant_id, customer_id);
            """
        )


def normalize_turns(turns: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Coerce raw turns to the canonical ``[{speaker, text}]`` shape, dropping empties.

    Tolerates ``direction`` (inbound/outbound) as an alias for speaker and any extra
    keys. An unrecognized speaker defaults to ``customer`` ONLY when a ``direction`` says
    inbound; otherwise the turn is kept with its stated speaker if valid, else skipped —
    we never invent who said what."""
    out: list[dict[str, str]] = []
    for t in turns or []:
        if not isinstance(t, dict):
            continue
        text = str(t.get("text") or "").strip()
        if not text:
            continue
        speaker = str(t.get("speaker") or "").strip().lower()
        if speaker not in _VALID_SPEAKERS:
            direction = str(t.get("direction") or "").strip().lower()
            if direction in ("inbound", "in", "customer", "lead"):
                speaker = SPEAKER_CUSTOMER
            elif direction in ("outbound", "out", "studio", "shop", "us"):
                speaker = SPEAKER_STUDIO
            else:
                continue
        out.append({"speaker": speaker, "text": text})
    return out


def upsert_conversation(
    tenant_id: str,
    customer_id: str,
    turns: list[dict[str, Any]],
    *,
    channel: str | None = None,
    source: str = "upload",
    campaign_message: str | None = None,
    dsn: str | None = None,
) -> str:
    """Persist (idempotently, keyed on tenant+customer) one lead's conversation thread.

    Returns the row id. Re-upserting the same lead REPLACES its turns (the latest
    upload is the source of truth) rather than appending duplicates."""
    ensure_schema(dsn)
    norm = normalize_turns(turns)
    conv_id = "conv_" + uuid.uuid4().hex[:16]
    with _connect(dsn) as conn:
        row = conn.execute(
            """
            INSERT INTO lead_conversations
                (id, tenant_id, customer_id, channel, source, campaign_message, turns)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, customer_id) DO UPDATE SET
                channel = EXCLUDED.channel,
                source = EXCLUDED.source,
                campaign_message = EXCLUDED.campaign_message,
                turns = EXCLUDED.turns,
                updated_at = now()
            RETURNING id
            """,
            (
                conv_id, tenant_id, customer_id, channel, source, campaign_message,
                json.dumps(norm),
            ),
        ).fetchone()
    return row["id"] if row else conv_id


def append_turn(
    tenant_id: str,
    customer_id: str,
    text: str,
    *,
    speaker: str = SPEAKER_CUSTOMER,
    channel: str | None = None,
    source: str = "inbound",
    dsn: str | None = None,
) -> tuple[str, bool]:
    """Append ONE real turn to a lead's stored conversation (create the row if absent).

    This is the INBOUND-signal write path (CustomerAcq-tlv.2): unlike
    :func:`upsert_conversation` (bulk upload, REPLACES turns), this never discards
    history — it appends exactly one ``{speaker, text}`` turn. Returns
    ``(conversation_id, appended)``; ``appended`` is False when the row already ends
    with this exact turn (webhook redelivery), so a retried delivery is idempotent.
    The append itself is a single atomic UPDATE (no read-modify-write race)."""
    clean = (text or "").strip()
    if not clean:
        raise ValueError("turn text is empty")
    if speaker not in _VALID_SPEAKERS:
        raise ValueError(f"speaker {speaker!r} not in {sorted(_VALID_SPEAKERS)}")
    ensure_schema(dsn)
    turn = {"speaker": speaker, "text": clean}
    turn_json = json.dumps(turn)
    conv_id = "conv_" + uuid.uuid4().hex[:16]
    with _connect(dsn) as conn:
        created = conn.execute(
            """
            INSERT INTO lead_conversations
                (id, tenant_id, customer_id, channel, source, turns)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (tenant_id, customer_id) DO NOTHING
            RETURNING id
            """,
            (conv_id, tenant_id, customer_id, channel, source, json.dumps([turn])),
        ).fetchone()
        if created is not None:
            return created["id"], True
        # Row exists: atomic dedupe-append — append ONLY when the thread does not
        # already end with this exact turn (idempotent on webhook redelivery).
        appended = conn.execute(
            """
            UPDATE lead_conversations
            SET turns = turns || %s::jsonb,
                channel = COALESCE(channel, %s),
                updated_at = now()
            WHERE tenant_id = %s AND customer_id = %s
              AND (jsonb_array_length(turns) = 0 OR turns->-1 <> %s::jsonb)
            RETURNING id
            """,
            (turn_json, channel, tenant_id, customer_id, turn_json),
        ).fetchone()
        if appended is not None:
            return appended["id"], True
        row = conn.execute(
            "SELECT id FROM lead_conversations "
            "WHERE tenant_id = %s AND customer_id = %s LIMIT 1",
            (tenant_id, customer_id),
        ).fetchone()
    return (row["id"] if row else conv_id), False


def get_conversation(
    tenant_id: str, customer_id: str, *, dsn: str | None = None
) -> dict[str, Any] | None:
    """The stored conversation for one lead, or ``None`` when none exists (honest —
    never a fabricated thread). Shape: ``{turns, channel, source, campaign_message}``."""
    ensure_schema(dsn)
    with _connect(dsn) as conn:
        row = conn.execute(
            "SELECT channel, source, campaign_message, turns FROM lead_conversations "
            "WHERE tenant_id = %s AND customer_id = %s LIMIT 1",
            (tenant_id, customer_id),
        ).fetchone()
    if row is None:
        return None
    turns = row["turns"]
    if isinstance(turns, str):
        try:
            turns = json.loads(turns)
        except Exception:
            turns = []
    return {
        "turns": normalize_turns(turns if isinstance(turns, list) else []),
        "channel": row["channel"],
        "source": row["source"],
        "campaign_message": row["campaign_message"],
    }
