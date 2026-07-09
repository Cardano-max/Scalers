"""Supervisor fleet board + patrol (the initech pattern: status / patrol / stall
detection for the campaign agents). Pure classification tests always run; the
DB roundtrip needs Postgres."""

from __future__ import annotations

import os
import uuid

import pytest

from studio.supervisor_fleet import (
    STALL_AFTER_SECONDS,
    _classify,
    fleet_status,
    patrol_once,
)

_pg = pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)


# ── pure: activity classification ──────────────────────────────────────────── #


def test_classify_working_stalled_and_starting():
    assert _classify("running", False, 10.0) == "working"
    assert _classify("running", False, STALL_AFTER_SECONDS + 1) == "stalled"
    assert _classify("running", False, None) == "starting"


def test_classify_terminal_wins_over_stale_selection():
    # A finished run with a never-resolved 'awaiting' selection row is done, not
    # waiting — terminal status must win.
    assert _classify("completed", True, 10.0) == "done"
    assert _classify("failed", True, 10.0) == "failed"
    assert _classify("running", True, 10.0) == "waiting-operator"


# ── DB: board + patrol roundtrip ───────────────────────────────────────────── #


def _seed_run(conn, run_id: str, tenant: str, *, status: str, step_age: str) -> None:
    conn.execute(
        "INSERT INTO runs (run_id, tenant_id, type, trigger, status) "
        "VALUES (%s, %s, 'campaign', 'test', %s)",
        (run_id, tenant, status),
    )
    conn.execute(
        "INSERT INTO agent_runs (id, campaign_id, run_id, role, model, input, output, created_at) "
        "VALUES (%s, %s, %s, 'draft', 'test', '{}'::jsonb, '{}'::jsonb, "
        f"now() - interval '{step_age}')",
        ("agr_" + uuid.uuid4().hex[:16], run_id, run_id),
    )


@pytest.mark.integration
@_pg
def test_fleet_board_and_patrol_records_stall_once():
    import psycopg

    tenant = "t_fleet_" + uuid.uuid4().hex[:6]
    stalled_run = "run_fleet_stalled_" + uuid.uuid4().hex[:6]
    fresh_run = "run_fleet_fresh_" + uuid.uuid4().hex[:6]
    with psycopg.connect(os.environ["ENGINE_DATABASE_URL"], autocommit=True) as conn:
        _seed_run(conn, stalled_run, tenant, status="running", step_age="20 minutes")
        _seed_run(conn, fresh_run, tenant, status="running", step_age="5 seconds")

    board = {r["run_id"]: r for r in fleet_status(tenant)}
    assert board[stalled_run]["activity"] == "stalled"
    assert board[fresh_run]["activity"] == "working"
    assert board[stalled_run]["last_role"] == "draft"

    # Patrol surfaces the stall and records ONE supervisor step for it.
    summary = patrol_once(tenant)
    stall_findings = [f for f in summary["new_findings"] if f["run_id"] == stalled_run]
    assert stall_findings and stall_findings[0]["rule"] == "stalled"
    # Exactly-once per (run, rule): a second sweep is silent for this stall.
    again = patrol_once(tenant)
    assert not [f for f in again["new_findings"] if f["run_id"] == stalled_run and f["rule"] == "stalled"]

    with psycopg.connect(os.environ["ENGINE_DATABASE_URL"], autocommit=True) as conn:
        n = conn.execute(
            "SELECT count(*) FROM agent_runs WHERE run_id=%s AND role='supervisor' "
            "AND input->>'patrol'='stalled'",
            (stalled_run,),
        ).fetchone()[0]
        assert n == 1
        # Cleanup — this tenant's rows only.
        conn.execute("DELETE FROM agent_runs WHERE run_id IN (%s, %s)", (stalled_run, fresh_run))
        conn.execute("DELETE FROM runs WHERE tenant_id=%s", (tenant,))
