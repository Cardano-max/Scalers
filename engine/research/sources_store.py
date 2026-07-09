"""The ``research_sources`` store — persist real, citable Firecrawl search hits.

One row per real source URL the REAL research step (the Studio research agent)
collected for a run. These are the *citable evidence* the research span points
at; the synthesis findings cite these URLs and the console renders WHERE the
agent searched.

Thin psycopg layer over ``infra/initdb/09-research-sources.sql`` (the single
source of truth for the schema), DSN from ``ENGINE_DATABASE_URL`` — the same
pattern as :mod:`actions.store` (``08-actions.sql``).

HONESTY GATE: every row comes from a real Firecrawl ``/v1/search`` response.
:func:`record_sources` persists exactly what the provider returned — never an
invented URL/title/snippet. An honest-empty research run persists NO rows.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

# infra/initdb/09-research-sources.sql relative to this file
# (engine/research/sources_store.py): parents[0]=research, [1]=engine, [2]=src.
_SOURCES_SQL = (
    Path(__file__).resolve().parents[2] / "infra" / "initdb" / "09-research-sources.sql"
)


def _dsn() -> str:
    return os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def _connect(dsn: str | None = None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(dsn or _dsn(), autocommit=True, row_factory=dict_row)


def ensure_schema(dsn: str | None = None) -> None:
    """Apply ``09-research-sources.sql`` (idempotent ``CREATE TABLE IF NOT EXISTS``)."""
    with _connect(dsn) as conn:
        conn.execute(_SOURCES_SQL.read_text(encoding="utf-8"))


def record_sources(
    *,
    run_id: str,
    tenant_id: str,
    sources: list[dict[str, Any]],
    dsn: str | None = None,
) -> list[str]:
    """Persist real Firecrawl search hits as ``research_sources`` rows; return ids.

    Each ``source`` dict carries ``{query, url, title, snippet}`` straight off the
    provider response. ``fetched_at`` defaults to ``now()``. No-op (returns ``[]``)
    when ``sources`` is empty — an honest-empty research step persists nothing
    rather than a fabricated row. A source with no real ``url`` is skipped (a row
    with no URL is not a citable source).
    """
    if not sources:
        return []
    ids: list[str] = []
    with _connect(dsn) as conn, conn.transaction():
        for s in sources:
            url = (s.get("url") or "").strip()
            if not url:
                continue
            sid = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO research_sources "
                "(id, run_id, tenant_id, query, url, title, snippet) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (sid, run_id, tenant_id, s.get("query"), url, s.get("title"), s.get("snippet")),
            )
            ids.append(sid)
    return ids


def list_sources(run_id: str, *, dsn: str | None = None) -> list[dict[str, Any]]:
    """Return the persisted sources for a run (console/verification read model)."""
    with _connect(dsn) as conn:
        rows = conn.execute(
            "SELECT id, run_id, tenant_id, query, url, title, snippet, fetched_at "
            "FROM research_sources WHERE run_id = %s ORDER BY fetched_at",
            (run_id,),
        ).fetchall()
    return [dict(r) for r in rows]
