"""First-party ``MemoryStore`` over the existing ``memories`` table.

Why this and not a framework: the live DB already ships a purpose-built
``memories(id, tenant_id, subject_type, subject_id, text, embedding vector(384),
metadata jsonb, content_hash, created_at)`` table with a unique natural key
``(tenant_id, subject_type, COALESCE(subject_id,''), content_hash)`` and a
``subject_type`` CHECK over ``{customer, campaign, conversation, fact}``. We
already have the hard parts — a REAL 384-dim semantic embedder
(:mod:`kb.embedding`), the cosine-retrieval pattern (:meth:`kb.store.KbStore.
voice_exemplars`), and the idempotent-upsert pattern (:mod:`research.sources_store`).
A memory framework would only add LLM-driven write/consolidation, which can be
layered later without re-platforming. So v1 is this ~1-file store: zero new deps,
``engine/**`` only.

HONESTY GATE:
* Embedding dim mismatch FAILS LOUDLY (never truncated / faked).
* ``recall`` on an empty / new tenant returns ``[]`` (never raises, never invents).
* A memory row is internal context, never an ``actions`` row — nothing here sends.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

from kb.embedding import Embedder, default_embedder, to_pgvector

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

# Mirror the live ``memories_subject_type_check`` CHECK constraint exactly so a
# bad subject_type fails in Python with a clear message rather than a DB 23514.
VALID_SUBJECT_TYPES: frozenset[str] = frozenset(
    {"customer", "campaign", "conversation", "fact"}
)


@dataclass(frozen=True)
class Memory:
    """One retrieved memory row. ``similarity`` is ``1 - cosine_distance`` (higher =
    closer); it is ``None`` for plain (non-similarity) reads."""

    text: str
    subject_type: str
    subject_id: str | None
    metadata: dict[str, Any] = field(default_factory=dict)
    similarity: float | None = None


def _dsn(dsn: str | None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def _content_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class MemoryStore:
    """Synchronous, tenant-scoped read/write over the ``memories`` table.

    Sync (psycopg) by design — the Studio tools offload it via ``asyncio.to_thread``
    exactly like the existing ``_persist_plan`` path. The embedder defaults to the
    REAL semantic model; offline/hermetic runs select the deterministic stub via
    ``$SCALERS_EMBEDDER`` (see :func:`kb.embedding.make_embedder`)."""

    def __init__(self, dsn: str | None = None, embedder: Embedder | None = None) -> None:
        self._dsn = _dsn(dsn)
        self._embedder = embedder or default_embedder()

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self._dsn, autocommit=True, row_factory=dict_row)

    def ensure_schema(self) -> None:
        """Idempotently ensure the ``memories`` table + indexes exist.

        The table already exists in the live cluster (created by infra), but this
        ``CREATE TABLE IF NOT EXISTS`` makes the schema reproducible on a fresh DB
        and is a no-op against the live one. Requires the pgvector extension
        (``01-pgvector.sql``)."""
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id            TEXT PRIMARY KEY,
                    tenant_id     TEXT NOT NULL,
                    subject_type  TEXT NOT NULL
                        CHECK (subject_type IN ('customer','campaign','conversation','fact')),
                    subject_id    TEXT,
                    text          TEXT NOT NULL,
                    embedding     vector(384),
                    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
                    content_hash  TEXT NOT NULL,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS memories_tenant_idx
                    ON memories (tenant_id);
                CREATE INDEX IF NOT EXISTS memories_subject_idx
                    ON memories (tenant_id, subject_type, subject_id);
                CREATE UNIQUE INDEX IF NOT EXISTS memories_natural_key
                    ON memories (tenant_id, subject_type, COALESCE(subject_id, ''), content_hash);
                """
            )

    def write(
        self,
        *,
        tenant_id: str,
        subject_type: str,
        subject_id: str | None,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Write (idempotently upsert) one memory and return its id.

        Idempotent on the natural key ``(tenant_id, subject_type,
        COALESCE(subject_id,''), content_hash)`` — re-writing the same fact refreshes
        the row instead of duplicating. The text is embedded with the REAL 384-dim
        model; a dim mismatch raises (never truncates)."""
        if subject_type not in VALID_SUBJECT_TYPES:
            raise ValueError(
                f"subject_type {subject_type!r} not in {sorted(VALID_SUBJECT_TYPES)}"
            )
        if not (text or "").strip():
            raise ValueError("memory text is empty")
        from psycopg.types.json import Json

        vec = self._embedder.embed(text)
        if len(vec) != 384:
            raise ValueError(f"embedding dim {len(vec)} != 384 (column is vector(384))")
        chash = _content_hash({"text": text, "subject_id": subject_id or ""})
        mem_id = "mem_" + uuid.uuid4().hex[:16]
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO memories
                    (id, tenant_id, subject_type, subject_id, text, embedding,
                     metadata, content_hash)
                VALUES (%s, %s, %s, %s, %s, %s::vector, %s, %s)
                ON CONFLICT (tenant_id, subject_type, COALESCE(subject_id, ''), content_hash)
                DO UPDATE SET text = EXCLUDED.text,
                              embedding = EXCLUDED.embedding,
                              metadata = EXCLUDED.metadata
                RETURNING id
                """,
                (
                    mem_id, tenant_id, subject_type, subject_id, text,
                    to_pgvector(vec), Json(metadata or {}), chash,
                ),
            ).fetchone()
        return row["id"]

    def recall(
        self,
        *,
        tenant_id: str,
        query: str,
        subject_type: str | None = None,
        subject_id: str | None = None,
        k: int = 5,
    ) -> list[Memory]:
        """Top-``k`` memories nearest to ``query`` by cosine similarity, tenant-scoped
        and optionally filtered to a subject. Empty / new tenant returns ``[]``
        (never raises). Mirrors :meth:`kb.store.KbStore.voice_exemplars`."""
        qvec = to_pgvector(self._embedder.embed(query))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT text, subject_type, subject_id, metadata,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM memories
                WHERE tenant_id = %s
                  AND (%s::text IS NULL OR subject_type = %s)
                  AND (%s::text IS NULL OR subject_id = %s)
                  AND embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector ASC
                LIMIT %s
                """,
                (
                    qvec, tenant_id,
                    subject_type, subject_type,
                    subject_id, subject_id,
                    qvec, k,
                ),
            ).fetchall()
        return [
            Memory(
                text=r["text"],
                subject_type=r["subject_type"],
                subject_id=r["subject_id"],
                metadata=r["metadata"] or {},
                similarity=float(r["similarity"]),
            )
            for r in rows
        ]

    def list_for_subject(
        self, *, tenant_id: str, subject_type: str, subject_id: str | None
    ) -> list[Memory]:
        """All memories for one subject (newest first) — no similarity ranking. Used
        by the dynamic-instruction injection and verification reads."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT text, subject_type, subject_id, metadata, created_at
                FROM memories
                WHERE tenant_id = %s AND subject_type = %s
                  AND COALESCE(subject_id, '') = COALESCE(%s, '')
                ORDER BY created_at DESC
                """,
                (tenant_id, subject_type, subject_id),
            ).fetchall()
        return [
            Memory(
                text=r["text"],
                subject_type=r["subject_type"],
                subject_id=r["subject_id"],
                metadata=r["metadata"] or {},
            )
            for r in rows
        ]
