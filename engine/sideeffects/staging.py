"""Per-RECIPIENT exactly-once staging for SMS drafts (CustomerAcq-t90.3, layer a).

The bead's DB proof: exactly-once was per-ACTION only (run-scoped idempotency
key), so one lead accumulated 3 byte-identical pending drafts and one address
took 10 sends. This module makes staging per-RECIPIENT:

* the outbox's partial UNIQUE ``(tenant_id, target, draft_md5) WHERE unsettled``
  (``14-suppression-consent.sql``) enforces one unsettled draft per
  (tenant, recipient, content) AT THE DATABASE — a conflict returns the
  EXISTING id, exactly like the boundary's idempotency-conflict path, and it
  holds even if a caller derives its key differently (the run-scoped-key bug
  cannot recur);
* a per-(tenant, target) MAX-PENDING cap bounds how many DISTINCT drafts can
  pile up for one recipient, serialized under a pg advisory xact lock so
  concurrent stagings cannot race past it (crash windows W5/W6 — see
  :mod:`suppression.ledger` for the full enumeration).

The send-time frequency backstop (layer b) is :func:`suppression.ledger.send_backstop`.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any

import psycopg
from psycopg.rows import dict_row

from sideeffects.keys import Channel, idempotency_key

__all__ = ["StageResult", "StageStatus", "stage_sms_draft"]


class StageStatus(Enum):
    STAGED = "staged"                # this call created the outbox row
    DUPLICATE = "duplicate"          # identical draft already staged/settled — existing id returned
    CAP_EXCEEDED = "cap_exceeded"    # too many distinct pending drafts for this recipient


@dataclass(frozen=True)
class StageResult:
    """``outbox_id`` is the created (STAGED) or existing (DUPLICATE) row.
    Edge: a DUPLICATE can carry ``outbox_id=None`` when the conflicting row
    was staged under a differently-derived key AND settled between our
    conflict and the lookup — the content still sent exactly once; callers
    must tolerate the missing id."""

    status: StageStatus
    outbox_id: int | None
    key: str


def _dsn(dsn: str | None) -> str:
    return dsn or os.environ.get(
        "ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers"
    )


def stage_sms_draft(
    *,
    tenant_id: str,
    target: str,
    draft: str,
    payload: dict[str, Any] | None = None,
    max_pending: int = 3,
    dsn: str | None = None,
) -> StageResult:
    """Stage one SMS draft into the outbox with per-recipient exactly-once.

    Byte-identical content for the same recipient collapses to ONE row (the
    existing id is returned, never a second row). Distinct drafts stage until
    ``max_pending`` unsettled rows exist for (tenant, target); beyond that the
    staging is refused with ``CAP_EXCEEDED``. The whole check-and-insert runs
    under a per-(tenant, target) advisory xact lock, so the cap cannot be
    raced, and the dedupe itself is the DB unique index — never check-then-act.
    """
    key = idempotency_key(tenant_id, Channel.SMS, target, draft)
    draft_md5 = hashlib.md5(draft.encode("utf-8")).hexdigest()
    body = payload if payload is not None else {"body": draft, "target": target}

    with psycopg.connect(_dsn(dsn), row_factory=dict_row) as conn:  # explicit txn
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"sms-stage:{tenant_id}:{target}",),
        )
        inserted = conn.execute(
            "INSERT INTO outbox (idempotency_key, channel, payload, status,"
            " tenant_id, target, draft_md5)"
            " VALUES (%s, %s, %s, 'PENDING', %s, %s, %s)"
            " ON CONFLICT DO NOTHING RETURNING id",
            (key, Channel.SMS.value, json.dumps(body), tenant_id, target, draft_md5),
        ).fetchone()
        if inserted is None:
            # Conflict — global idempotency key (same content, any status) or the
            # partial recipient/content index (same content staged under a
            # differently-derived key). Return the existing row's id.
            conn.rollback()
            existing = _find_existing(conn, key, tenant_id, target, draft_md5)
            return StageResult(StageStatus.DUPLICATE, existing, key)
        n = conn.execute(
            "SELECT count(*) AS n FROM outbox WHERE tenant_id=%s AND target=%s"
            " AND status IN ('PENDING','SENDING')",
            (tenant_id, target),
        ).fetchone()["n"]
        if n > max_pending:
            conn.rollback()  # the insert above never becomes visible
            return StageResult(StageStatus.CAP_EXCEEDED, None, key)
        conn.commit()
        return StageResult(StageStatus.STAGED, inserted["id"], key)


def _find_existing(
    conn: psycopg.Connection[Any], key: str, tenant_id: str, target: str, draft_md5: str
) -> int | None:
    row = conn.execute(
        "SELECT id FROM outbox WHERE idempotency_key = %s", (key,)
    ).fetchone()
    if row is not None:
        return row["id"]
    row = conn.execute(
        "SELECT id FROM outbox WHERE tenant_id=%s AND target=%s AND draft_md5=%s"
        " AND status IN ('PENDING','SENDING') LIMIT 1",
        (tenant_id, target, draft_md5),
    ).fetchone()
    return row["id"] if row is not None else None
