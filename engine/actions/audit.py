"""The ``send_audit`` store — a durable record of operator-initiated campaign sends.

One row per campaign-level send decision: a ``send_eligible`` batch entry, or an
``override`` of a draft that did NOT pass the confidence/compliance bar. The
override is the sensitive case — it is the only way a below-bar / flagged draft
reaches the real send path, so it MUST be auditable (who, why, the snapshot of the
confidence/escalation it overrode).

Thin psycopg layer over ``infra/initdb/10-send-audit.sql`` (the single source of
truth for the schema), DSN from ``ENGINE_DATABASE_URL`` — the same pattern as
:mod:`actions.store` and :mod:`research.sources_store`. The actual send is NOT done
here; this only records it. The send goes through
:func:`actions.publish.approve_and_publish` (atomic exactly-once claim + gmail
allow-list/redirect).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

# infra/initdb/10-send-audit.sql relative to engine/actions/audit.py:
# parents[0]=actions, [1]=engine, [2]=src.
_AUDIT_SQL = (
    Path(__file__).resolve().parents[2] / "infra" / "initdb" / "10-send-audit.sql"
)


def _dsn(dsn: str | None = None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def _connect(dsn: str | None = None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), autocommit=True, row_factory=dict_row)


def ensure_schema(dsn: str | None = None) -> None:
    """Apply ``10-send-audit.sql`` (idempotent ``CREATE TABLE IF NOT EXISTS``)."""
    with _connect(dsn) as conn:
        conn.execute(_AUDIT_SQL.read_text(encoding="utf-8"))


def record_send_audit(
    *,
    action_id: str,
    kind: str,
    run_id: str | None = None,
    tenant_id: str | None = None,
    operator: str | None = None,
    reason: str | None = None,
    eligible: bool | None = None,
    conf: float | None = None,
    threshold: float | None = None,
    esc_kind: str | None = None,
    result: str | None = None,
    mode: str | None = None,
    dsn: str | None = None,
) -> str:
    """Persist one operator send-decision audit row; return its id. Best-effort
    schema-ensure first so the row never fails on a fresh DB.

    ``mode`` is the send mode the action resolved to ('live' | 'test_redirect'); it is
    NULL for a pre-send audit (the override row written BEFORE the send runs)."""
    ensure_schema(dsn)
    aud_id = f"aud_{uuid.uuid4().hex[:16]}"
    with _connect(dsn) as conn:
        conn.execute(
            "INSERT INTO send_audit "
            "(id, action_id, run_id, tenant_id, kind, operator, reason, eligible, "
            " conf, threshold, esc_kind, result, mode) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (aud_id, action_id, run_id, tenant_id, kind, operator, reason, eligible,
             conf, threshold, esc_kind, result, mode),
        )
    return aud_id


def list_send_audit(
    *, action_id: str | None = None, run_id: str | None = None, dsn: str | None = None
) -> list[dict[str, Any]]:
    """The audit trail, newest first — filtered by action or run when given."""
    ensure_schema(dsn)
    where = []
    params: list[Any] = []
    if action_id:
        where.append("action_id = %s")
        params.append(action_id)
    if run_id:
        where.append("run_id = %s")
        params.append(run_id)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with _connect(dsn) as conn:
        rows = conn.execute(
            "SELECT id, action_id, run_id, tenant_id, kind, operator, reason, eligible, "
            "conf, threshold, esc_kind, result, mode, created_at "
            f"FROM send_audit{clause} ORDER BY created_at DESC",
            tuple(params),
        ).fetchall()
    return [dict(r) for r in rows]
