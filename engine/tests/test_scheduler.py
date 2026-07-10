"""Approve-and-schedule: recording, refusals, and the due-publish sweep. The
publish path is exercised through the REAL approve_and_publish seam contract
by faking the store one level down (same pattern as test_campaign_send)."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import studio.scheduler as sched

_pg = pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)


def test_schedule_rejects_past_and_unparseable(monkeypatch):
    monkeypatch.setattr(
        "actions.store.get_action",
        lambda action_id, dsn=None: type("A", (), {"status": "pending"})(),
    )
    with pytest.raises(ValueError, match="in the past"):
        sched.schedule_action(
            "act_x", datetime.now(timezone.utc) - timedelta(hours=1)
        )
    with pytest.raises(ValueError, match="unparseable"):
        sched.schedule_action("act_x", "tomorrow 9am")


def test_schedule_refuses_non_pending(monkeypatch):
    monkeypatch.setattr(
        "actions.store.get_action",
        lambda action_id, dsn=None: type("A", (), {"status": "sent"})(),
    )
    with pytest.raises(ValueError, match="PENDING"):
        sched.schedule_action(
            "act_x", datetime.now(timezone.utc) + timedelta(hours=1)
        )


@pytest.mark.integration
@_pg
def test_schedule_roundtrip_and_due_publish_gate_clears():
    """A due scheduled draft is swept; a gate refusal CLEARS the schedule so it
    never retries forever (the refusal reason lives on last_error)."""
    import psycopg

    from actions.publish import TestModeSendBlockedError

    dsn = os.environ["ENGINE_DATABASE_URL"]
    aid = "act_schedtest_" + uuid.uuid4().hex[:8]
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO actions (id, tenant_id, type, channel, draft, status, target) "
            "VALUES (%s, 't_sched', 'email', 'gmail', 'body', 'pending', 'x@t.test')",
            (aid,),
        )
    try:
        out = sched.schedule_action(
            aid, datetime.now(timezone.utc) + timedelta(seconds=1), live=False, dsn=dsn
        )
        assert out["actionId"] == aid and out["live"] is False

        # Force due, then sweep with a gate-refusing publish.
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(
                "UPDATE actions SET scheduled_for = now() - interval '1 minute' WHERE id=%s",
                (aid,),
            )
        assert aid in sched.due_actions(dsn=dsn)

        import actions.publish as publish

        def _refuse(action_id, dsn=None, live=False, connectors=None):
            raise TestModeSendBlockedError("TEST MODE - refused")

        orig = publish.approve_and_publish
        publish.approve_and_publish = _refuse
        try:
            swept = sched.publish_due(dsn=dsn)
        finally:
            publish.approve_and_publish = orig
        assert any(b["actionId"] == aid for b in swept["blocked"])
        # Schedule cleared — the next sweep is silent for this row.
        assert aid not in sched.due_actions(dsn=dsn)
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM actions WHERE id=%s", (aid,))
