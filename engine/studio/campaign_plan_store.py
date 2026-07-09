"""Persistence for the AG-UI campaign-plan SHARED STATE (Phase 3.1-backend).

One row per studio session holds the latest :class:`studio.agui.CampaignPlan` as
JSONB. Mirrors ``studio/chat_store.py``: lazy psycopg, idempotent
``CREATE TABLE IF NOT EXISTS`` so ``setup()`` is a no-op on an existing cluster.

Schema (``campaign_plans``):

* ``id``          — ``plan_<session>`` (one live plan per session; upsert by id).
* ``session_id``  — the studio session this plan belongs to.
* ``state``       — JSONB snapshot of the CampaignPlan.
* ``created_at`` / ``updated_at`` — TIMESTAMPTZ; ``updated_at`` bumps on every edit.
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
    """Create ``campaign_plans`` if absent (idempotent)."""
    with _connect(dsn) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS campaign_plans (
                id          TEXT PRIMARY KEY,
                session_id  TEXT        NOT NULL,
                state       JSONB       NOT NULL DEFAULT '{}'::jsonb,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS campaign_plans_session_idx
                ON campaign_plans (session_id);
            """
        )


def _plan_id(session_id: str) -> str:
    return f"plan_{session_id}"


def upsert_plan(session_id: str, state: dict[str, Any], *, dsn: str | None = None) -> str:
    """Insert-or-update this session's plan state; bump ``updated_at``. Returns id."""
    setup(dsn)
    pid = _plan_id(session_id)
    with _connect(dsn) as conn:
        conn.execute(
            """
            INSERT INTO campaign_plans (id, session_id, state)
            VALUES (%s, %s, %s::jsonb)
            ON CONFLICT (id) DO UPDATE
                SET state = EXCLUDED.state, updated_at = now()
            """,
            (pid, session_id, json.dumps(state)),
        )
    return pid


def latest_plans(
    n: int = 2, *, session_id: str | None = None, dsn: str | None = None
) -> list[dict[str, Any]]:
    """Most-recently-updated plans (optionally for one session), newest first."""
    with _connect(dsn) as conn:
        if session_id is None:
            rows = conn.execute(
                "SELECT id, session_id, state, updated_at FROM campaign_plans "
                "ORDER BY updated_at DESC LIMIT %s",
                (n,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, session_id, state, updated_at FROM campaign_plans "
                "WHERE session_id = %s ORDER BY updated_at DESC LIMIT %s",
                (session_id, n),
            ).fetchall()
    return list(rows)
