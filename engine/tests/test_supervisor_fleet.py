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


# ── DB: in-flight runs (agent_runs steps, NO runs row yet) ─────────────────── #


def _seed_steps_only(conn, run_id: str, steps: list[tuple[str, str]]) -> None:
    """An EXECUTING run: agent steps landing in agent_runs while the runs row has
    not been materialized yet (the studio executor writes it once, at completion)."""
    for role, age in steps:
        conn.execute(
            "INSERT INTO agent_runs (id, campaign_id, run_id, role, model, input, "
            "output, created_at) VALUES (%s, %s, %s, %s, 'test', '{}'::jsonb, "
            "'{}'::jsonb, now() - %s::interval)",
            ("agr_" + uuid.uuid4().hex[:16], run_id, run_id, role, age),
        )


@pytest.mark.integration
@_pg
def test_fleet_sees_in_flight_runs_and_never_leaks_foreign_ones():
    """Truth-gap fix 4: /studio/fleet only listed MATERIALIZED runs rows (written at
    completion), so an executing run was invisible to the supervisor board. The board
    must now UNION in-flight runs from the run's own live agent_runs steps — the real
    incrementally-written source — marked ``in_flight: true`` with real step counts,
    attributed to the tenant via the run's campaign_blueprints row. A run whose
    tenant evidence points elsewhere must NOT leak in."""
    import psycopg

    from studio.blueprint_store import setup as bp_setup, upsert_blueprint

    dsn = os.environ["ENGINE_DATABASE_URL"]
    tenant = "t_fleet_inflight_" + uuid.uuid4().hex[:6]
    other = "t_fleet_foreign_" + uuid.uuid4().hex[:6]
    rid = "run_inflight_" + uuid.uuid4().hex[:6]
    rid_foreign = "run_inflight_foreign_" + uuid.uuid4().hex[:6]
    rid_done = "run_fleet_done_" + uuid.uuid4().hex[:6]
    with psycopg.connect(dsn, autocommit=True) as conn:
        _seed_steps_only(conn, rid, [("planner", "30 seconds"), ("draft", "2 seconds")])
        _seed_steps_only(conn, rid_foreign, [("planner", "10 seconds")])
        # A materialized (completed) run for contrast: it must read in_flight=False.
        _seed_run(conn, rid_done, tenant, status="completed", step_age="1 minute")
    bp_setup(dsn)
    upsert_blueprint(rid, {"goal": "x"}, tenant_id=tenant, dsn=dsn)
    upsert_blueprint(rid_foreign, {"goal": "x"}, tenant_id=other, dsn=dsn)

    try:
        board = {r["run_id"]: r for r in fleet_status(tenant, dsn=dsn)}
        assert rid in board, "an executing run must be visible on the board"
        row = board[rid]
        assert row["in_flight"] is True
        assert row["status"] == "running"
        assert row["activity"] == "working"  # steps landed seconds ago
        assert row["n_steps"] == 2  # REAL step count, not a placeholder
        assert row["last_role"] == "draft"
        # The other tenant's in-flight run must not leak onto this board.
        assert rid_foreign not in board
        # Materialized runs stay marked honestly.
        assert board[rid_done]["in_flight"] is False
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(
                "DELETE FROM agent_runs WHERE run_id IN (%s, %s, %s)",
                (rid, rid_foreign, rid_done),
            )
            conn.execute(
                "DELETE FROM campaign_blueprints WHERE run_id IN (%s, %s)",
                (rid, rid_foreign),
            )
            conn.execute("DELETE FROM runs WHERE tenant_id=%s", (tenant,))
