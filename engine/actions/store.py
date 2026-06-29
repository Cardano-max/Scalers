"""The ``actions`` table store (OBS review queue) — record / list / get / update.

One row per proposed side-effecting action (an outreach email, a post, a comment
reply). The engine's decision path writes a PENDING row when an action routes to
REVIEW; the console reads them as the review queue; :mod:`actions.publish` flips
the lifecycle on approve. The jury card / confidence / gates are NOT duplicated
here — they live on the linked ``autonomy_decisions`` row (joined by ``decision_id``).

Thin psycopg layer over ``infra/initdb/08-actions.sql`` (the single source of
truth for the schema), DSN from ``ENGINE_DATABASE_URL``. ``idempotency_key`` is
UNIQUE, so :func:`record_pending_action` is idempotent — re-seeding the same
logical action returns the existing id instead of creating a duplicate.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

# infra/initdb/08-actions.sql relative to this file (engine/actions/store.py):
# parents[0]=actions, [1]=engine, [2]=src.
_ACTIONS_SQL = Path(__file__).resolve().parents[2] / "infra" / "initdb" / "08-actions.sql"

# Every column on the actions table, in declaration order (08-actions.sql).
_COLUMNS: tuple[str, ...] = (
    "id", "tenant_id", "decision_id", "run_id", "type", "channel", "worker",
    "target", "subject", "context", "draft", "status", "autonomy", "conf",
    "threshold", "esc_kind", "esc_label", "idempotency_key", "deep_link",
    "outcome_label", "outcome_kind", "recommend", "thinking", "engagement",
    "last_error", "created_at", "updated_at", "approved_at", "sent_at",
)

# Columns :func:`update_status` is allowed to set (whitelist — the **kwargs keys
# are interpolated as identifiers, so they must never be attacker-controlled).
_UPDATABLE: frozenset[str] = frozenset({
    "decision_id", "run_id", "worker", "target", "subject", "context", "draft",
    "autonomy", "conf", "threshold", "esc_kind", "esc_label", "deep_link",
    "outcome_label", "outcome_kind", "recommend", "last_error",
    "approved_at", "sent_at",
})


@dataclass
class ActionRow:
    """One ``actions`` row (the console's Action/Approval/Activity shape)."""

    id: str
    tenant_id: str
    type: str
    channel: str
    draft: str
    status: str
    decision_id: str | None = None
    run_id: str | None = None
    worker: str | None = None
    target: str | None = None
    subject: str | None = None
    context: str | None = None
    autonomy: str | None = None
    conf: float | None = None
    threshold: float | None = None
    esc_kind: str | None = None
    esc_label: str | None = None
    idempotency_key: str | None = None
    deep_link: str | None = None
    outcome_label: str | None = None
    outcome_kind: str | None = None
    recommend: str | None = None
    thinking: list[Any] = field(default_factory=list)
    engagement: list[Any] = field(default_factory=list)
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    approved_at: datetime | None = None
    sent_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "ActionRow":
        return cls(**{k: row.get(k) for k in _COLUMNS})


def _dsn() -> str:
    return os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def _connect(dsn: str | None = None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(dsn or _dsn(), autocommit=True, row_factory=dict_row)


def ensure_schema(dsn: str | None = None) -> None:
    """Apply ``08-actions.sql`` (idempotent ``CREATE TABLE IF NOT EXISTS``)."""
    with _connect(dsn) as conn:
        conn.execute(_ACTIONS_SQL.read_text(encoding="utf-8"))


def record_pending_action(
    *,
    tenant_id: str,
    decision_id: str | None,
    type: str,  # noqa: A002 — matches the column name / seam contract
    channel: str,
    worker: str | None,
    target: str | None,
    draft: str,
    subject: str | None = None,
    context: str | None = None,
    conf: float | None,
    threshold: float | None,
    esc_kind: str | None,
    esc_label: str | None,
    idempotency_key: str,
    run_id: str | None = None,
    dsn: str | None = None,
) -> str:
    """Insert a PENDING action and return its id. Idempotent on ``idempotency_key``:
    a duplicate insert returns the existing row's id (so re-seeding never dupes)."""
    action_id = f"act_{uuid.uuid4().hex[:16]}"
    with _connect(dsn) as conn:
        row = conn.execute(
            """
            INSERT INTO actions (
                id, tenant_id, decision_id, run_id, type, channel, worker,
                target, subject, context, draft, status, conf, threshold,
                esc_kind, esc_label, idempotency_key)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s,%s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id
            """,
            (
                action_id, tenant_id, decision_id, run_id, type, channel, worker,
                target, subject, context, draft, conf, threshold,
                esc_kind, esc_label, idempotency_key,
            ),
        ).fetchone()
        if row is not None:
            return row["id"]
        # UNIQUE conflict: the logical action already exists — return its id.
        existing = conn.execute(
            "SELECT id FROM actions WHERE idempotency_key = %s", (idempotency_key,)
        ).fetchone()
        return existing["id"]


def list_actions(tenant_id: str, status: str | None = None, dsn: str | None = None) -> list[ActionRow]:
    """All actions for a tenant (optionally filtered by status), newest first."""
    with _connect(dsn) as conn:
        if status is None:
            rows = conn.execute(
                "SELECT * FROM actions WHERE tenant_id = %s ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM actions WHERE tenant_id = %s AND status = %s "
                "ORDER BY created_at DESC",
                (tenant_id, status),
            ).fetchall()
    return [ActionRow.from_row(r) for r in rows]


def get_action(action_id: str, dsn: str | None = None) -> ActionRow | None:
    with _connect(dsn) as conn:
        row = conn.execute("SELECT * FROM actions WHERE id = %s", (action_id,)).fetchone()
    return ActionRow.from_row(row) if row is not None else None


def update_status(action_id: str, status: str, *, dsn: str | None = None, **fields: Any) -> ActionRow | None:
    """Set ``status`` (+ any whitelisted ``fields``) and bump ``updated_at``.

    Returns the updated row. ``fields`` keys must be in :data:`_UPDATABLE` — they
    are interpolated as SQL identifiers, so an unknown key is rejected rather than
    risk injection."""
    bad = set(fields) - _UPDATABLE
    if bad:
        raise ValueError(f"non-updatable action field(s): {sorted(bad)}")
    assignments = ["status = %s", "updated_at = now()"]
    values: list[Any] = [status]
    for col, val in fields.items():
        assignments.append(f"{col} = %s")
        values.append(val)
    values.append(action_id)
    with _connect(dsn) as conn:
        row = conn.execute(
            f"UPDATE actions SET {', '.join(assignments)} WHERE id = %s RETURNING *",
            values,
        ).fetchone()
    return ActionRow.from_row(row) if row is not None else None
