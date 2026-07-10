"""Approve-and-schedule — operator-approved deferred publishing.

Scheduling is an APPROVAL GESTURE with a timestamp: the operator points at one
specific PENDING draft and says "publish this at time T" (optionally live). The
row records ``scheduled_for`` + ``schedule_live``; a background loop publishes
due rows through the SAME :func:`actions.publish.approve_and_publish` every
other send uses — exactly-once claim, tenant TEST-MODE gate, allow-list /
redirect, all intact. There is deliberately NO bulk scheduling and NO way for
an agent to schedule anything: the schedule route/tool is operator-initiated,
and the chat tool is approval-gated.

Failure honesty: a gate-refused publish CLEARS the schedule (no silent retry
forever — the refusal reason is on the row's ``last_error``); a delivery
failure keeps the publish path's own honest failed/error state.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

# Bounded per tick so a pathological backlog can't monopolize the loop.
MAX_PUBLISH_PER_TICK = 10


def _dsn(dsn: str | None) -> str:
    return dsn or os.environ.get(
        "ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers"
    )


def _connect(dsn: str | None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), row_factory=dict_row, autocommit=True)


def schedule_action(
    action_id: str,
    when: str | datetime,
    *,
    live: bool = False,
    dsn: str | None = None,
) -> dict[str, Any]:
    """Record the operator's schedule on ONE pending draft. Refuses non-pending
    rows and past timestamps (>5 min grace) — a schedule must be a real future
    intent, not a backdoor immediate send."""
    from actions.store import get_action, update_status

    if isinstance(when, str):
        try:
            when_dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"unparseable schedule time {when!r}") from exc
    else:
        when_dt = when
    if when_dt.tzinfo is None:
        when_dt = when_dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if (when_dt - now).total_seconds() < -300:
        raise ValueError(f"schedule time {when_dt.isoformat()} is in the past")

    action = get_action(action_id, dsn=dsn)
    if action is None:
        raise ValueError(f"no action {action_id!r}")
    if action.status != "pending":
        raise ValueError(
            f"only a PENDING draft can be scheduled (action {action_id} is "
            f"{action.status!r})"
        )
    row = update_status(
        action_id, "pending", dsn=dsn,
        scheduled_for=when_dt, schedule_live=bool(live),
    )
    return {
        "actionId": action_id,
        "scheduledFor": when_dt.isoformat(),
        "live": bool(live),
        "target": getattr(row, "target", None),
        "channel": getattr(row, "channel", None),
    }


def cancel_schedule(action_id: str, *, dsn: str | None = None) -> bool:
    """Clear a pending schedule. True if a schedule was actually cleared."""
    with _connect(dsn) as conn:
        row = conn.execute(
            "UPDATE actions SET scheduled_for = NULL, schedule_live = FALSE, "
            "updated_at = now() WHERE id = %s AND scheduled_for IS NOT NULL "
            "RETURNING id",
            (action_id,),
        ).fetchone()
    return row is not None


def due_actions(*, dsn: str | None = None, limit: int = MAX_PUBLISH_PER_TICK) -> list[str]:
    """Ids of PENDING drafts whose schedule time has arrived, oldest first."""
    with _connect(dsn) as conn:
        rows = conn.execute(
            "SELECT id FROM actions WHERE status = 'pending' "
            "AND scheduled_for IS NOT NULL AND scheduled_for <= now() "
            "ORDER BY scheduled_for LIMIT %s",
            (limit,),
        ).fetchall()
    return [r["id"] for r in rows]


def publish_due(*, dsn: str | None = None) -> dict[str, Any]:
    """One scheduler sweep: publish every due draft through the REAL approve
    path. Per-draft isolation — one failure never blocks the rest."""
    from actions.publish import TestModeSendBlockedError, approve_and_publish
    from actions.store import get_action

    published: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for action_id in due_actions(dsn=dsn):
        action = get_action(action_id, dsn=dsn)
        live = bool(getattr(action, "schedule_live", False)) if action else False
        try:
            row = approve_and_publish(action_id, dsn=dsn, live=live)
            published.append({
                "actionId": action_id,
                "result": getattr(row, "status", None),
                "mode": getattr(row, "mode", None),
                "target": getattr(row, "target", None),
            })
        except TestModeSendBlockedError as exc:
            # Gate refused: clear the schedule so it never silently retries; the
            # refusal reason is already on the row (publish wrote last_error).
            cancel_schedule(action_id, dsn=dsn)
            blocked.append({"actionId": action_id, "reason": str(exc)})
        except Exception as exc:  # noqa: BLE001 — per-draft isolation
            cancel_schedule(action_id, dsn=dsn)
            failed.append({"actionId": action_id, "error": f"{type(exc).__name__}: {exc}"})
    return {"published": published, "blocked": blocked, "failed": failed}


async def start_scheduler_loop(*, dsn: str | None = None) -> None:
    """Background loop: sweep for due drafts every ``ACTION_SCHEDULER_SECONDS``
    (default 60; 0 disables). Per-tick errors are logged, never fatal."""
    import asyncio

    try:
        interval = float(os.environ.get("ACTION_SCHEDULER_SECONDS", "60") or 0)
    except ValueError:
        interval = 60.0
    if interval <= 0:
        return
    while True:
        try:
            out = await asyncio.to_thread(publish_due, dsn=dsn)
            if out["published"] or out["blocked"] or out["failed"]:
                print(f"[action-scheduler] {json.dumps(out, default=str)}", flush=True)
        except Exception as exc:  # noqa: BLE001 — the loop must survive any tick
            print(f"[action-scheduler] tick failed: {type(exc).__name__}: {exc}", flush=True)
        await asyncio.sleep(interval)
