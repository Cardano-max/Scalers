"""Runs listing/detail TRUTH (harness-confirmed gaps 1+2).

* Duration: studio campaign runs materialize their ``runs`` row in ONE write at
  completion (created_at == updated_at), so the old created→updated derivation
  showed a fabricated "0.0s" over 34-65s of real work. The honest source is the
  run's own ``agent_runs`` step span; a run with no steps serves ``None`` — NEVER
  a fake 0.0s.
* Counts: the run row's "N staged" showed the agent-STEP count. The Run payload now
  carries the truthfully-named ``steps_total`` (agent_runs count) and a separate
  ``drafts_staged`` (REAL actions rows for the run).
"""

from __future__ import annotations

import os
import uuid

import pytest

_pg = pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)

pytestmark = pytest.mark.integration


def _seed_completed_run(conn, run_id: str, tenant: str) -> None:
    """The studio pattern: the runs row lands in ONE write at completion, so
    created_at == updated_at on the row itself."""
    conn.execute(
        "INSERT INTO runs (run_id, tenant_id, type, trigger, status) "
        "VALUES (%s, %s, 'campaign', 'test', 'completed')",
        (run_id, tenant),
    )


def _seed_step(conn, run_id: str, role: str, age: str) -> None:
    conn.execute(
        "INSERT INTO agent_runs (id, campaign_id, run_id, role, model, input, output, "
        "created_at) VALUES (%s, %s, %s, %s, 'test', '{}'::jsonb, '{}'::jsonb, "
        "now() - %s::interval)",
        ("agr_" + uuid.uuid4().hex[:16], run_id, run_id, role, age),
    )


@_pg
def test_run_duration_is_the_real_agent_runs_span_never_zero():
    import psycopg

    from obsapi import repo

    dsn = os.environ["ENGINE_DATABASE_URL"]
    tenant = "t_runtruth_" + uuid.uuid4().hex[:6]
    rid = "run_truth_" + uuid.uuid4().hex[:8]
    with psycopg.connect(dsn, autocommit=True) as conn:
        _seed_completed_run(conn, rid, tenant)
        _seed_step(conn, rid, "planner", "40 seconds")
        _seed_step(conn, rid, "jury", "0 seconds")
    try:
        run = repo.run(rid)
        assert run is not None
        # The row pair is ~0s (single write) — the served duration must come from
        # the run's own step span (~40s), never read "0.0s".
        assert run.duration is not None and run.duration != "0.0s"
        assert run.duration.endswith("s") and "m" not in run.duration
        secs = float(run.duration.rstrip("s"))
        assert 35.0 <= secs <= 45.0, f"expected ~40s from the step span, got {run.duration}"
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM agent_runs WHERE run_id=%s", (rid,))
            conn.execute("DELETE FROM runs WHERE run_id=%s", (rid,))


@_pg
def test_run_with_no_steps_serves_null_duration_not_zero():
    import psycopg

    from obsapi import repo

    dsn = os.environ["ENGINE_DATABASE_URL"]
    tenant = "t_runtruth_" + uuid.uuid4().hex[:6]
    rid = "run_truth_nostep_" + uuid.uuid4().hex[:8]
    with psycopg.connect(dsn, autocommit=True) as conn:
        _seed_completed_run(conn, rid, tenant)
    try:
        run = repo.run(rid)
        assert run is not None
        assert run.duration is None  # honest unknown — NEVER a fabricated "0.0s"
        assert run.steps_total == 0
        assert run.drafts_staged == 0
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM runs WHERE run_id=%s", (rid,))


@_pg
def test_steps_total_and_drafts_staged_are_separate_honest_counts():
    """The mislabel: 'N staged' showed the agent-STEP count (e.g. 12 steps) while the
    run's REAL drafts were 3-5. The payload now carries both, truthfully named."""
    import psycopg

    from actions.store import ensure_schema, record_pending_action
    from obsapi import repo

    dsn = os.environ["ENGINE_DATABASE_URL"]
    tenant = "t_runtruth_" + uuid.uuid4().hex[:6]
    rid = "run_truth_counts_" + uuid.uuid4().hex[:8]
    with psycopg.connect(dsn, autocommit=True) as conn:
        _seed_completed_run(conn, rid, tenant)
        for i, role in enumerate(("planner", "strategist", "draft", "critic", "jury")):
            _seed_step(conn, rid, role, f"{50 - i * 10} seconds")
    ensure_schema(dsn)
    action_ids = []
    for i in range(2):  # 2 REAL drafts vs 5 agent steps
        action_ids.append(
            record_pending_action(
                tenant_id=tenant,
                decision_id=None,
                type="outreach",
                channel="gmail",
                worker="test",
                target=f"lead{i}@example.com",
                draft="held draft",
                conf=None,
                threshold=None,
                esc_kind=None,
                esc_label=None,
                idempotency_key=f"{rid}:{i}",
                run_id=rid,
                dsn=dsn,
            )
        )
    try:
        run = repo.run(rid)
        assert run is not None
        assert run.steps_total == 5
        assert run.drafts_staged == 2
        assert run.steps_total != run.drafts_staged  # the exact conflation, pinned
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM actions WHERE run_id=%s", (rid,))
            conn.execute("DELETE FROM agent_runs WHERE run_id=%s", (rid,))
            conn.execute("DELETE FROM runs WHERE run_id=%s", (rid,))
