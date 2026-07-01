"""Persistence for the executable :class:`~studio.campaign_blueprint.CampaignBlueprint`
(P1.5 blueprint #1 — the plan the planner builds BEFORE drafting).

One row per campaign run holds the blueprint as JSONB, keyed to ``run_id`` (== Run.id).
Mirrors ``studio/campaign_spec_store.py``: lazy psycopg, idempotent
``CREATE TABLE IF NOT EXISTS`` so :func:`setup` is a no-op on an existing cluster.

Schema (``campaign_blueprints``):

* ``run_id``      — PK; the run this blueprint plans (== Run.id).
* ``campaign_id`` — the campaign id the agents ran under (agent_runs.campaign_id).
* ``tenant_id``   — owning tenant.
* ``session_id``  — the studio session that launched the run (may be NULL).
* ``planner_model``— the model tier the planner node actually ran at (honest; NULL if
                     the plan was built deterministically with no model call).
* ``state``       — JSONB snapshot of the CampaignBlueprint.
* ``created_at`` / ``updated_at`` — TIMESTAMPTZ.
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
    """Create ``campaign_blueprints`` if absent (idempotent)."""
    with _connect(dsn) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS campaign_blueprints (
                run_id        TEXT PRIMARY KEY,
                campaign_id   TEXT,
                tenant_id     TEXT,
                session_id    TEXT,
                planner_model TEXT,
                state         JSONB       NOT NULL DEFAULT '{}'::jsonb,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS campaign_blueprints_campaign_idx
                ON campaign_blueprints (campaign_id);
            """
        )


def upsert_blueprint(
    run_id: str,
    state: dict[str, Any],
    *,
    campaign_id: str | None = None,
    tenant_id: str | None = None,
    session_id: str | None = None,
    planner_model: str | None = None,
    dsn: str | None = None,
) -> str:
    """Insert-or-update this run's blueprint; bump ``updated_at``. Returns run_id."""
    setup(dsn)
    with _connect(dsn) as conn:
        conn.execute(
            """
            INSERT INTO campaign_blueprints
                (run_id, campaign_id, tenant_id, session_id, planner_model, state)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (run_id) DO UPDATE
                SET state = EXCLUDED.state,
                    campaign_id = COALESCE(EXCLUDED.campaign_id, campaign_blueprints.campaign_id),
                    tenant_id = COALESCE(EXCLUDED.tenant_id, campaign_blueprints.tenant_id),
                    session_id = COALESCE(EXCLUDED.session_id, campaign_blueprints.session_id),
                    planner_model = COALESCE(EXCLUDED.planner_model, campaign_blueprints.planner_model),
                    updated_at = now()
            """,
            (run_id, campaign_id, tenant_id, session_id, planner_model, json.dumps(state)),
        )
    return run_id


def get_blueprint(run_id: str, *, dsn: str | None = None) -> dict[str, Any] | None:
    """The stored blueprint row for a run (or ``None`` if absent / store unavailable)."""
    try:
        with _connect(dsn) as conn:
            row = conn.execute(
                "SELECT run_id, campaign_id, tenant_id, session_id, planner_model, "
                "state, updated_at FROM campaign_blueprints WHERE run_id = %s",
                (run_id,),
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
