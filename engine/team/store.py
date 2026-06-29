"""Durable store for the autonomous marketing TEAM (P1).

Mirrors the persistence pattern of :mod:`autonomy.store`: a thin store object
whose ``setup()`` creates its tables with ``CREATE TABLE IF NOT EXISTS`` (additive
only — it never ``ALTER``s or clobbers a table another module owns), psycopg
imported lazily, an autocommit ``dict_row`` connection, and small real read/write
methods.

Three additive tables (P1 team spine):

* ``agent_runs``    — one row per role invocation (which role, which model, the
  input it saw, the typed output it produced). The audit trail of *who did what*.
* ``assets``        — the artifacts the team produced. ``status`` starts at
  ``queued`` and is moved by the human-review path; the team NEVER writes a
  ``sent`` status (sends stay held / approve-first).
* ``asset_critiques`` — the critic's independent verdict per asset, FK to
  ``assets``.

These tables are NEW; nothing here touches ``autonomy_*``, ``runs``, ``actions``,
or any existing table.
"""

from __future__ import annotations

from typing import Any

# Statuses the TEAM is allowed to write. "sent" is deliberately absent: the
# orchestrator queues only; a human (review path) approves and a separate,
# already-gated send boundary is the only thing that may mark an asset shipped.
ASSET_STATUS_QUEUED = "queued"
ASSET_STATUS_PENDING_REVIEW = "pending_review"
ASSET_STATUS_APPROVED = "approved"
ASSET_STATUS_REJECTED = "rejected"


# The additive DDL, kept as a module constant so it can be applied either through
# TeamStore.setup() or directly (e.g. psql). All three are CREATE TABLE IF NOT
# EXISTS — re-running is a no-op and it never alters an existing table.
TEAM_DDL = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id          TEXT PRIMARY KEY,
    campaign_id TEXT        NOT NULL,
    run_id      TEXT        NOT NULL,
    role        TEXT        NOT NULL,
    model       TEXT        NOT NULL,
    input       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    output      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_runs_campaign_idx ON agent_runs (campaign_id);
CREATE INDEX IF NOT EXISTS agent_runs_run_idx      ON agent_runs (run_id);

CREATE TABLE IF NOT EXISTS assets (
    id          TEXT PRIMARY KEY,
    campaign_id TEXT        NOT NULL,
    asset_type  TEXT        NOT NULL,
    content     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    status      TEXT        NOT NULL DEFAULT 'queued',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS assets_campaign_idx ON assets (campaign_id);
CREATE INDEX IF NOT EXISTS assets_status_idx   ON assets (status);

CREATE TABLE IF NOT EXISTS asset_critiques (
    id           TEXT PRIMARY KEY,
    asset_id     TEXT        NOT NULL REFERENCES assets (id) ON DELETE CASCADE,
    critic_model TEXT        NOT NULL,
    rationale    TEXT        NOT NULL,
    verdict      TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS asset_critiques_asset_idx ON asset_critiques (asset_id);
"""


class TeamStore:
    """Postgres store for the team spine (agent_runs / assets / asset_critiques).

    psycopg is imported lazily so importing this module needs no driver and the
    in-memory/test paths stay clean.
    """

    def __init__(self, conninfo: str) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self._connect = lambda: psycopg.connect(
            conninfo, autocommit=True, row_factory=dict_row
        )

    def setup(self) -> None:
        """Apply the additive DDL (idempotent; safe to call on every boot)."""
        with self._connect() as conn:
            conn.execute(TEAM_DDL)

    # -- writes ------------------------------------------------------------- #

    def record_agent_run(
        self,
        *,
        id: str,
        campaign_id: str,
        run_id: str,
        role: str,
        model: str,
        input: Any,
        output: Any,
    ) -> None:
        """Persist one role invocation (audit: who did what, with which model)."""
        from psycopg.types.json import Json

        with self._connect() as conn:
            conn.execute(
                "INSERT INTO agent_runs (id, campaign_id, run_id, role, model, input, output) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                (id, campaign_id, run_id, role, model, Json(input), Json(output)),
            )

    def record_asset(
        self,
        *,
        id: str,
        campaign_id: str,
        asset_type: str,
        content: Any,
        status: str = ASSET_STATUS_QUEUED,
    ) -> None:
        """Queue a produced asset. Default status is 'queued' — sends stay held."""
        from psycopg.types.json import Json

        with self._connect() as conn:
            conn.execute(
                "INSERT INTO assets (id, campaign_id, asset_type, content, status) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                (id, campaign_id, asset_type, Json(content), status),
            )

    def record_critique(
        self,
        *,
        id: str,
        asset_id: str,
        critic_model: str,
        rationale: str,
        verdict: str,
    ) -> None:
        """Persist the critic's independent verdict for one asset."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO asset_critiques (id, asset_id, critic_model, rationale, verdict) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                (id, asset_id, critic_model, rationale, verdict),
            )

    # -- reads -------------------------------------------------------------- #

    def list_assets(self, campaign_id: str) -> list[dict[str, Any]]:
        """All assets for a campaign, oldest first."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM assets WHERE campaign_id=%s ORDER BY created_at",
                (campaign_id,),
            ).fetchall()

    def list_agent_runs(self, run_id: str) -> list[dict[str, Any]]:
        """All role invocations for one team run, oldest first."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM agent_runs WHERE run_id=%s ORDER BY created_at",
                (run_id,),
            ).fetchall()

    def get_critiques(self, asset_id: str) -> list[dict[str, Any]]:
        """All critiques recorded for one asset."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM asset_critiques WHERE asset_id=%s ORDER BY created_at",
                (asset_id,),
            ).fetchall()
