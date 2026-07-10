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
    "is_seeded", "scheduled_for", "schedule_live",
)

# Columns :func:`update_status` is allowed to set (whitelist — the **kwargs keys
# are interpolated as identifiers, so they must never be attacker-controlled).
_UPDATABLE: frozenset[str] = frozenset({
    "decision_id", "run_id", "worker", "target", "subject", "context", "draft",
    "autonomy", "conf", "threshold", "esc_kind", "esc_label", "deep_link",
    "outcome_label", "outcome_kind", "recommend", "last_error",
    "approved_at", "sent_at", "scheduled_for", "schedule_live",
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
    # PERSISTED seed marker (Slice-5 honesty gate): true only for demo/seed rows
    # (actions.seed_demo). The live decision path leaves it false; the console
    # badges true rows as "Seeded demo data — not a live jury run" so nothing
    # fabricated can masquerade as a live action.
    is_seeded: bool = False
    # Operator-approved deferred publish (27-action-schedule.sql): WHEN to publish
    # and whether the operator explicitly authorized a live (non-redirect) send.
    # The scheduler routes through approve_and_publish — every gate still applies.
    scheduled_for: datetime | None = None
    schedule_live: bool = False

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
    is_seeded: bool = False,
) -> str:
    """Insert a PENDING action and return its id. Idempotent on ``idempotency_key``:
    a duplicate insert returns the existing row's id (so re-seeding never dupes).

    ``is_seeded`` PERSISTS whether this row is demo/seed data (Slice-5 honesty
    gate). The live decision path (contentrun / engagement) leaves it False; only
    :mod:`actions.seed_demo` passes True. A True row is badged in the console and
    never silently shown as a live action."""
    action_id = f"act_{uuid.uuid4().hex[:16]}"
    with _connect(dsn) as conn:
        row = conn.execute(
            """
            INSERT INTO actions (
                id, tenant_id, decision_id, run_id, type, channel, worker,
                target, subject, context, draft, status, conf, threshold,
                esc_kind, esc_label, idempotency_key, is_seeded)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s,%s,%s)
            -- Bare ON CONFLICT so BOTH unique guards are absorbed silently (nmh.11):
            --   (1) idempotency_key UNIQUE  — same run_id:cust_id exactly-once (nmh.2);
            --   (2) the partial-unique recipient guard
            --       actions_pending_recipient_uniq (tenant_id, worker, target)
            --       WHERE status='pending' — ONE pending draft per recipient per
            --       worker, so a RETRY (fresh run_id) re-staging the same recipient is
            --       a structural no-op instead of a phantom-duplicate pending row.
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            (
                action_id, tenant_id, decision_id, run_id, type, channel, worker,
                target, subject, context, draft, conf, threshold,
                esc_kind, esc_label, idempotency_key, is_seeded,
            ),
        ).fetchone()
        if row is not None:
            return row["id"]
        # A UNIQUE conflict: the logical action already exists — return its id so the
        # caller's count stays honest and nothing is re-fired. Prefer the idempotency_key
        # match (same run replay); fall back to the already-pending recipient row (a
        # retry under a NEW run_id hits the recipient guard, not the idempotency key).
        existing = conn.execute(
            "SELECT id FROM actions WHERE idempotency_key = %s", (idempotency_key,)
        ).fetchone()
        if existing is None and target is not None:
            existing = conn.execute(
                "SELECT id FROM actions WHERE tenant_id = %s AND worker = %s "
                "AND target = %s AND status = 'pending' ORDER BY created_at LIMIT 1",
                (tenant_id, worker, target),
            ).fetchone()
        return existing["id"] if existing else action_id


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


def list_actions_for_run(
    run_id: str, *, status: str | None = None, dsn: str | None = None
) -> list[ActionRow]:
    """All actions staged under one campaign ``run_id`` (optionally a status),
    oldest first. Used by the campaign-level send to enumerate a run's drafts."""
    with _connect(dsn) as conn:
        if status is None:
            rows = conn.execute(
                "SELECT * FROM actions WHERE run_id = %s ORDER BY created_at",
                (run_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM actions WHERE run_id = %s AND status = %s "
                "ORDER BY created_at",
                (run_id, status),
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
