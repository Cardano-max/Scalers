"""GLOBAL practitioner-wisdom grounding store (bead 1mk.9, KNOW-02 partition).

The few-shot grounding side of the KB: verbatim practitioner sentences + DO/DON'T
rules, stored GLOBAL (brand-agnostic) and retrieved by the writing cells
(brand-voice S2, copywriter S5) to anchor output on authentic human phrasing
and avoid AI tells. Disjoint from the tenant-scoped eval store
(:class:`kb.store.KbStore`); it shares the embedder, the pgvector encoding, and
the same offline (no hot-path) posture.

VERBATIM is the asset: ``upsert`` stores ``text`` exactly as given and embeds it
unchanged. Re-ingest is idempotent on ``(partition, content_hash)``.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterator

import psycopg

from kb.embedding import EMBED_DIM, Embedder, default_embedder, to_pgvector

PARTITION = "practitioner-wisdom"

CATEGORIES = frozenset(
    {"general", "brand-voice", "hooks-cta", "research", "reply", "outreach", "do", "dont"}
)
KINDS = frozenset(
    {"testimonial", "curated-skill-description", "operator-note", "distilled-rule"}
)


@dataclass(frozen=True)
class WisdomSnippet:
    """One verbatim grounding row (+ a cosine distance when retrieved by similarity)."""

    id: str
    partition: str
    category: str
    kind: str
    text: str
    language: str
    source: dict[str, Any]
    content_hash: str
    applicability: str | None = None
    harvested_at: date | None = None
    created_at: datetime | None = None
    distance: float | None = None  # set only by similarity search; lower = closer


class GroundingStore:
    """Synchronous access to the GLOBAL ``practitioner_wisdom`` partition."""

    def __init__(self, dsn: str, embedder: Embedder | None = None) -> None:
        self._dsn = dsn
        # Default = the REAL semantic embedder (bge-small-en-v1.5); offline runs
        # opt into the deterministic stub via $SCALERS_EMBEDDER (make_embedder).
        self._embedder = embedder or default_embedder()

    @contextmanager
    def _conn(self) -> Iterator[psycopg.Connection]:
        conn = psycopg.connect(self._dsn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── write (offline loader path) ──────────────────────────────────────────

    def upsert(
        self,
        *,
        text: str,
        category: str,
        kind: str = "testimonial",
        language: str = "en",
        source: dict[str, Any] | None = None,
        content_hash: str,
        applicability: str | None = None,
        harvested_at: str | date | None = None,
        partition: str = PARTITION,
    ) -> str:
        """Insert (or refresh) one verbatim snippet. Idempotent on the natural
        key ``(partition, content_hash)`` — a re-load never dups. ``text`` is
        stored and embedded EXACTLY as given (no normalization)."""
        if category not in CATEGORIES:
            raise ValueError(f"category {category!r} not in {sorted(CATEGORIES)}")
        if kind not in KINDS:
            raise ValueError(f"kind {kind!r} not in {sorted(KINDS)}")
        vec = self._embedder.embed(text)
        if len(vec) != EMBED_DIM:
            raise ValueError(f"embedding dim {len(vec)} != {EMBED_DIM} (column is vector({EMBED_DIM}))")

        with self._conn() as conn:
            row = conn.execute(
                "INSERT INTO practitioner_wisdom"
                " (partition, category, kind, text, language, source, applicability,"
                "  content_hash, embedding, harvested_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s)"
                " ON CONFLICT (partition, content_hash) DO UPDATE SET"
                "   category = EXCLUDED.category, kind = EXCLUDED.kind,"
                "   language = EXCLUDED.language, source = EXCLUDED.source,"
                "   applicability = EXCLUDED.applicability,"
                "   embedding = EXCLUDED.embedding, harvested_at = EXCLUDED.harvested_at"
                " RETURNING id",
                (
                    partition, category, kind, text, language,
                    json.dumps(source or {}), applicability, content_hash,
                    to_pgvector(vec), harvested_at,
                ),
            ).fetchone()
            return str(row[0])

    def load_entries(self, entries: list[dict[str, Any]]) -> int:
        """Bulk-load JSONL rows (as emitted by ``build_practitioner_wisdom``).
        Returns the number ingested. Idempotent: re-running loads the same set
        with no duplicates."""
        n = 0
        for e in entries:
            self.upsert(
                text=e["text"],
                category=e["category"],
                kind=e.get("kind", "testimonial"),
                language=e.get("language", "en"),
                source=e.get("source"),
                content_hash=e["content_hash"],
                applicability=e.get("applicability"),
                harvested_at=e.get("harvested_at"),
                partition=e.get("partition", PARTITION),
            )
            n += 1
        return n

    # ── read (grounding path — what S2 / S5 call) ────────────────────────────

    def _hydrate(self, r: tuple, *, with_distance: bool = False) -> WisdomSnippet:
        return WisdomSnippet(
            id=str(r[0]), partition=r[1], category=r[2], kind=r[3], text=r[4],
            language=r[5], source=r[6] or {}, applicability=r[7],
            content_hash=r[8], harvested_at=r[9], created_at=r[10],
            distance=(r[11] if with_distance else None),
        )

    _COLS = (
        "id, partition, category, kind, text, language, source, applicability,"
        " content_hash, harvested_at, created_at"
    )

    def list(
        self,
        *,
        category: str | None = None,
        kind: str | None = None,
        partition: str = PARTITION,
    ) -> list[WisdomSnippet]:
        """List snippets (optionally filtered by category/kind), newest first.
        Returns ``[]`` for an empty partition — never raises."""
        clauses = ["partition = %s"]
        params: list[Any] = [partition]
        if category is not None:
            clauses.append("category = %s")
            params.append(category)
        if kind is not None:
            clauses.append("kind = %s")
            params.append(kind)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT {self._COLS} FROM practitioner_wisdom"
                " WHERE " + " AND ".join(clauses) + " ORDER BY created_at DESC, id",
                params,
            ).fetchall()
        return [self._hydrate(r) for r in rows]

    def retrieve(
        self,
        query: str,
        *,
        k: int = 5,
        category: str | None = None,
        partition: str = PARTITION,
    ) -> list[WisdomSnippet]:
        """Top-``k`` verbatim snippets nearest to ``query`` by cosine distance —
        the few-shot grounding call. Optionally constrained to one ``category``
        (e.g. the brand-voice cell asks for ``category='brand-voice'``). Empty
        partition returns ``[]``."""
        qvec = to_pgvector(self._embedder.embed(query))
        # Param order follows %s appearance in the SQL: the SELECT's distance
        # expression first, then the WHERE filters, then LIMIT.
        clauses = ["partition = %s", "embedding IS NOT NULL"]
        where_params: list[Any] = [partition]
        if category is not None:
            clauses.append("category = %s")
            where_params.append(category)
        params: list[Any] = [qvec, *where_params, k]
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT {self._COLS}, embedding <=> %s::vector AS distance"
                " FROM practitioner_wisdom WHERE " + " AND ".join(clauses)
                + " ORDER BY distance ASC LIMIT %s",
                params,
            ).fetchall()
        return [self._hydrate(r, with_distance=True) for r in rows]

    def count(self, *, partition: str = PARTITION) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT count(*) FROM practitioner_wisdom WHERE partition = %s",
                (partition,),
            ).fetchone()
        return int(row[0])
