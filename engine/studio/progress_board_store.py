"""Persistence for the durable :class:`~studio.progress_board.ProgressBoard`
(P1.5 blueprint #3 — the structured run-state the UI renders + replanning reads).

One row per campaign run holds the LATEST board snapshot as JSONB, keyed to ``run_id``.
Mirrors ``studio/campaign_spec_store.py``: lazy psycopg, idempotent
``CREATE TABLE IF NOT EXISTS`` so :func:`setup` is a no-op on an existing cluster.

Schema (``progress_boards``):

* ``run_id``     — PK; the run this board tracks (== Run.id).
* ``tenant_id``  — owning tenant.
* ``state``      — JSONB snapshot of the ProgressBoard.
* ``created_at`` / ``updated_at`` — TIMESTAMPTZ; ``updated_at`` bumps on every snapshot.
"""

from __future__ import annotations

import json
import os
from typing import Any

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"


def _dsn(dsn: str | None) -> str:
    return (
        dsn
        or os.environ.get("ENGINE_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or _DEFAULT_DSN
    )


def _connect(dsn: str | None = None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), autocommit=True, row_factory=dict_row)


def setup(dsn: str | None = None) -> None:
    """Create ``progress_boards`` if absent (idempotent)."""
    with _connect(dsn) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS progress_boards (
                run_id      TEXT PRIMARY KEY,
                tenant_id   TEXT,
                state       JSONB       NOT NULL DEFAULT '{}'::jsonb,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )


def upsert_board(
    run_id: str, state: dict[str, Any], *, tenant_id: str | None = None,
    dsn: str | None = None,
) -> str:
    """Insert-or-update this run's latest board snapshot; bump ``updated_at``."""
    setup(dsn)
    with _connect(dsn) as conn:
        conn.execute(
            """
            INSERT INTO progress_boards (run_id, tenant_id, state)
            VALUES (%s, %s, %s::jsonb)
            ON CONFLICT (run_id) DO UPDATE
                SET state = EXCLUDED.state,
                    tenant_id = COALESCE(EXCLUDED.tenant_id, progress_boards.tenant_id),
                    updated_at = now()
            """,
            (run_id, tenant_id, json.dumps(state)),
        )
    return run_id


def get_board(run_id: str, *, dsn: str | None = None) -> dict[str, Any] | None:
    """The stored board row for a run (or ``None`` if absent / store unavailable)."""
    try:
        with _connect(dsn) as conn:
            row = conn.execute(
                "SELECT run_id, tenant_id, state, updated_at FROM progress_boards "
                "WHERE run_id = %s",
                (run_id,),
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
