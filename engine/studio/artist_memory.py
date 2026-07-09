"""Artist-scoped memories over the EXISTING ``memories`` table.

The Studio's memory layer (:class:`memory.MemoryStore`) predates artists as a
subject: its ``subject_type`` CHECK covers customer/campaign/conversation/fact
only. Artist events (a new design uploaded, an operator note about an artist)
belong on the SAME table so recall/inventory read one store — this module:

  * widens the ``memories_subject_type_check`` constraint to include ``'artist'``
    (idempotent runtime DDL, twinned by ``infra/initdb/22-memories-artist-subject.sql``);
  * writes artist memories with the SAME natural-key idempotency + REAL embedding
    discipline as :meth:`memory.MemoryStore.write` (an embedder failure degrades to
    a NULL embedding — the memory is still listed chronologically, it just isn't
    semantically recallable; never a fabricated vector);
  * lists one artist's memories newest-first for the console + supervisor tools.

``subject_id`` is the artist SLUG (see :func:`studio.artists_directory.artist_slug`)
so the API, the upload path, and the tools address one canonical key.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from typing import Any

ARTIST_SUBJECT_TYPE = "artist"

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

# Idempotent widening of the live CHECK constraint (safe on fresh + existing DBs).
_WIDEN_SQL = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'memories'::regclass
          AND conname  = 'memories_subject_type_check'
          AND pg_get_constraintdef(oid) NOT LIKE '%%artist%%'
    ) THEN
        ALTER TABLE memories DROP CONSTRAINT memories_subject_type_check;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'memories'::regclass
          AND conname  = 'memories_subject_type_check'
    ) THEN
        ALTER TABLE memories ADD CONSTRAINT memories_subject_type_check
            CHECK (subject_type IN
                   ('customer','campaign','conversation','fact','artist'));
    END IF;
END $$;
"""


def _dsn(dsn: str | None = None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def _connect(dsn: str | None = None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), autocommit=True, row_factory=dict_row)


def ensure_artist_memory_schema(dsn: str | None = None) -> None:
    """Ensure the ``memories`` table exists (via the canonical store) and its
    subject-type CHECK admits ``'artist'``. Idempotent; concurrent-safe enough for
    the request path (a lost race re-runs a no-op DO block)."""
    from memory import MemoryStore

    MemoryStore(dsn=_dsn(dsn)).ensure_schema()
    with _connect(dsn) as conn:
        conn.execute(_WIDEN_SQL)


def _embed_or_none(text: str) -> str | None:
    """The REAL 384-dim embedding as a pgvector literal, or ``None`` when the
    embedder is unavailable / misconfigured (honest degradation — a NULL embedding
    is listable but not semantically recallable; never a fabricated vector)."""
    try:
        from kb.embedding import default_embedder, to_pgvector

        vec = default_embedder().embed(text)
        if len(vec) != 384:
            return None
        return to_pgvector(vec)
    except Exception:
        return None


def write_artist_memory(
    tenant_id: str,
    artist_slug: str,
    text: str,
    *,
    metadata: dict[str, Any] | None = None,
    is_test: bool = False,
    dsn: str | None = None,
) -> str:
    """Write (idempotently upsert) one artist memory; returns the memory id.

    Same natural-key discipline as :meth:`memory.MemoryStore.write` — re-writing the
    identical text for the same artist refreshes the row instead of duplicating."""
    if not (tenant_id or "").strip():
        raise ValueError("tenant_id is required")
    if not (artist_slug or "").strip():
        raise ValueError("artist_slug is required (memories are per-artist)")
    if not (text or "").strip():
        raise ValueError("memory text is empty")
    from psycopg.types.json import Json

    ensure_artist_memory_schema(dsn)
    payload = {"text": text, "subject_id": artist_slug}
    chash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    mem_id = "mem_" + uuid.uuid4().hex[:16]
    vec = _embed_or_none(text)
    with _connect(dsn) as conn:
        row = conn.execute(
            """
            INSERT INTO memories
                (id, tenant_id, subject_type, subject_id, text, embedding,
                 metadata, content_hash, is_test)
            VALUES (%s, %s, %s, %s, %s, %s::vector, %s, %s, %s)
            ON CONFLICT (tenant_id, subject_type, COALESCE(subject_id, ''), content_hash)
            DO UPDATE SET text = EXCLUDED.text,
                          metadata = EXCLUDED.metadata,
                          is_test = EXCLUDED.is_test
            RETURNING id
            """,
            (
                mem_id, tenant_id, ARTIST_SUBJECT_TYPE, artist_slug, text,
                vec, Json(metadata or {}), chash, is_test,
            ),
        ).fetchone()
    return row["id"]


def list_artist_memories(
    tenant_id: str, artist_slug: str, *, limit: int = 50, dsn: str | None = None
) -> list[dict[str, Any]]:
    """One artist's memories, newest first: ``[{id, at, text, metadata}]``. Honest
    ``[]`` on an empty subject or an unreadable store — never invented."""
    try:
        with _connect(dsn) as conn:
            rows = conn.execute(
                """
                SELECT id, text, metadata, created_at FROM memories
                WHERE tenant_id=%s AND subject_type=%s
                  AND COALESCE(subject_id,'')=%s AND is_test=FALSE
                ORDER BY created_at DESC LIMIT %s
                """,
                (tenant_id, ARTIST_SUBJECT_TYPE, artist_slug, int(limit)),
            ).fetchall()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        ca = r.get("created_at")
        out.append(
            {
                "id": r.get("id"),
                "at": ca.isoformat() if hasattr(ca, "isoformat") else str(ca),
                "text": r.get("text"),
                "metadata": r.get("metadata") or {},
            }
        )
    return out
