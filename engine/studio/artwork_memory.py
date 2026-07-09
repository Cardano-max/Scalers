"""Artist artwork memory — per-image VLM tags + summary + pgvector embedding
(CustomerAcq-nmh.5, spec §3/§4).

Each analyzed portfolio image becomes one durable, tenant-scoped ``artwork_memory``
row: the structured :class:`~studio.artwork_vision.ArtworkAnalysis` (as JSONB tags), a
human summary, and a ``vector(384)`` embedding of the summary text — so a campaign can
retrieve "the 4 best lion pieces" by meaning, not just filename. The embedding column
matches every other embedding in the system (``kb.embedding.EMBED_DIM == 384``) so the
real FastEmbed model is a drop-in for the deterministic offline stub.

Scoping / gates:
* **tenant-scoped** — every read/write is filtered by ``tenant_id`` (skindesign for the
  real client); a query for one tenant never returns another's artwork.
* **is_test** — real-client rows carry ``is_test=FALSE`` (the operator's own assets);
  test/fixture rows carry ``is_test=TRUE`` and are excluded from real reads by default,
  mirroring the ``memories.is_test`` isolation (wwy.9).
* **idempotent** — the row id is deterministic from ``(tenant, artist, image_ref)`` so
  re-ingesting a portfolio refreshes rather than duplicates.
* **company-owned only / HELD** — this stores memory; it sends nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

from kb.embedding import EMBED_DIM, Embedder, default_embedder, to_pgvector
from studio.artwork_vision import ArtworkAnalysis, analysis_summary

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

_SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS artwork_memory (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    artist_id    TEXT NOT NULL,
    image_ref    TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'upload',
    media_type   TEXT,
    tags         JSONB NOT NULL,
    summary      TEXT NOT NULL,
    embedding    vector(384),
    is_test      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS artwork_memory_tenant_artist_idx
    ON artwork_memory (tenant_id, artist_id);
"""


def _dsn(dsn: str | None = None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def _connect(dsn: str | None = None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), autocommit=True, row_factory=dict_row)


def ensure_schema(dsn: str | None = None) -> None:
    """Create the ``artwork_memory`` table + pgvector extension (idempotent)."""
    with _connect(dsn) as conn:
        conn.execute(_SCHEMA_SQL)


def artwork_id(tenant_id: str, artist_id: str, image_ref: str) -> str:
    """Deterministic row id so re-ingesting the same image refreshes, never duplicates."""
    h = hashlib.sha256(f"{tenant_id}|{artist_id}|{image_ref}".encode()).hexdigest()[:16]
    return f"art_{h}"


def _vec_literal(vec: list[float]) -> str:
    if len(vec) != EMBED_DIM:
        raise ValueError(f"embedding dim {len(vec)} != {EMBED_DIM} (column is vector({EMBED_DIM}))")
    return to_pgvector(vec)


@dataclass
class ArtworkRecord:
    """One stored artwork-memory row (read view)."""

    id: str
    tenant_id: str
    artist_id: str
    image_ref: str
    source: str
    media_type: str | None
    tags: dict[str, Any]
    summary: str
    is_test: bool

    @property
    def analysis(self) -> ArtworkAnalysis:
        """Rehydrate the structured analysis from the stored tags."""
        return ArtworkAnalysis.model_validate(self.tags)


def record_artwork(
    tenant_id: str,
    artist_id: str,
    image_ref: str,
    analysis: ArtworkAnalysis,
    *,
    source: str = "upload",
    media_type: str | None = None,
    is_test: bool = False,
    embedder: Embedder | None = None,
    dsn: str | None = None,
) -> str:
    """Upsert one analyzed artwork into the artist's memory. Returns the row id.

    The embedding is over the analysis summary + style tags (the retrieval text).
    Idempotent on the deterministic id — re-ingest refreshes tags/summary/embedding."""
    if not tenant_id or not artist_id or not image_ref:
        raise ValueError("tenant_id, artist_id, image_ref are all required")
    embedder = embedder or default_embedder()
    summary = analysis_summary(analysis)
    embed_text = summary + " " + " ".join(analysis.style_tags)
    vec = _vec_literal(embedder.embed(embed_text))
    rid = artwork_id(tenant_id, artist_id, image_ref)
    with _connect(dsn) as conn:
        conn.execute(
            "INSERT INTO artwork_memory"
            " (id, tenant_id, artist_id, image_ref, source, media_type, tags, summary,"
            "  embedding, is_test)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s)"
            " ON CONFLICT (id) DO UPDATE SET"
            "   tags=EXCLUDED.tags, summary=EXCLUDED.summary, embedding=EXCLUDED.embedding,"
            "   source=EXCLUDED.source, media_type=EXCLUDED.media_type, updated_at=now()",
            (rid, tenant_id, artist_id, image_ref, source, media_type,
             json.dumps(analysis.model_dump()), summary, vec, is_test),
        )
    return rid


def _row_to_record(r: dict[str, Any]) -> ArtworkRecord:
    tags = r["tags"]
    if isinstance(tags, str):
        tags = json.loads(tags)
    return ArtworkRecord(
        id=r["id"], tenant_id=r["tenant_id"], artist_id=r["artist_id"],
        image_ref=r["image_ref"], source=r["source"], media_type=r.get("media_type"),
        tags=tags, summary=r["summary"], is_test=r["is_test"],
    )


def list_artwork(
    tenant_id: str, artist_id: str, *, include_test: bool = False, dsn: str | None = None
) -> list[ArtworkRecord]:
    """All artwork memory for an artist (newest first). Real reads exclude test rows."""
    clause = "" if include_test else " AND is_test = FALSE"
    with _connect(dsn) as conn:
        rows = conn.execute(
            "SELECT * FROM artwork_memory WHERE tenant_id=%s AND artist_id=%s"
            + clause + " ORDER BY created_at DESC",
            (tenant_id, artist_id),
        ).fetchall()
    return [_row_to_record(r) for r in rows]


@dataclass
class ArtworkHit:
    record: ArtworkRecord
    similarity: float


def search_artwork(
    tenant_id: str,
    artist_id: str,
    query: str,
    *,
    k: int = 4,
    include_test: bool = False,
    embedder: Embedder | None = None,
    dsn: str | None = None,
) -> list[ArtworkHit]:
    """Top-``k`` artwork for a campaign query (e.g. 'lion strength'), ranked by pgvector
    cosine similarity of the query against each piece's analysis embedding. Tenant +
    artist scoped; real reads exclude test rows. Honest-empty when the artist has no
    artwork on file."""
    embedder = embedder or default_embedder()
    vec = _vec_literal(embedder.embed(query))
    clause = "" if include_test else " AND is_test = FALSE"
    with _connect(dsn) as conn:
        rows = conn.execute(
            "SELECT *, 1 - (embedding <=> %s::vector) AS similarity"
            " FROM artwork_memory WHERE tenant_id=%s AND artist_id=%s AND embedding IS NOT NULL"
            + clause + " ORDER BY embedding <=> %s::vector ASC LIMIT %s",
            (vec, tenant_id, artist_id, vec, max(1, int(k))),
        ).fetchall()
    return [ArtworkHit(record=_row_to_record(r), similarity=float(r["similarity"])) for r in rows]
