"""PG-integration tests for the proactive scanner's ``scheduled_job_runs`` ledger
(CustomerAcq-fr1.1 AC-1 + AC-9).

The ledger is CLAIM-THEN-TRANSITION, not claim-only:
  * a fire_date is CLAIMED exactly once via INSERT ... ON CONFLICT DO NOTHING
    (UNIQUE(tenant_id, job_id, fire_date)) — a double-fire on the same fire_date
    yields ONE run (AC-1);
  * the claimed row then TRANSITIONS to completed/failed — a crash after claim
    leaves a 'claimed' row that a restart can detect (``stale_claimed``) and
    re-drive or surface, so a crash NEVER silently consumes the fire_date (AC-9).

Requires a real local Postgres (RUN_PG_TESTS / ENGINE_DATABASE_URL).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pytest

from tests.conftest import private_schema

pytestmark = pytest.mark.skipif(
    not os.getenv("ENGINE_DATABASE_URL"), reason="requires Postgres"
)


@pytest.fixture()
def ledger():
    """A ScheduleLedger bound to a fresh private schema with the ledger DDL applied."""
    from proactive.schedule_ledger import ScheduleLedger

    with private_schema("18-scheduled-job-runs.sql") as sch:
        yield ScheduleLedger(sch.dsn)


TENANT = "sdt-test"
JOB = "daily_scan"


def test_claim_is_exactly_once_per_fire_date(ledger):
    """Two claims of the same (tenant, job, fire_date) => one owner, same run id."""
    fd = date(2026, 7, 3)
    first = ledger.claim(TENANT, JOB, fd)
    second = ledger.claim(TENANT, JOB, fd)

    assert first.is_new is True
    assert first.status == "claimed"
    assert second.is_new is False  # lost the race: row already existed
    assert second.run_id == first.run_id  # both resolve to the one real run
    assert len(ledger.list_runs(TENANT, JOB)) == 1


def test_double_fire_same_date_one_run(ledger):
    """Simulated concurrent fire on the same date: exactly one is_new claim."""
    fd = date(2026, 7, 3)
    results = [ledger.claim(TENANT, JOB, fd) for _ in range(5)]
    assert sum(1 for r in results if r.is_new) == 1
    rows = [r for r in ledger.list_runs(TENANT, JOB) if r.fire_date == fd]
    assert len(rows) == 1


def test_claim_then_complete_transition(ledger):
    fd = date(2026, 7, 3)
    claim = ledger.claim(TENANT, JOB, fd)
    assert ledger.complete(claim.run_id, detail={"proposals": 2}) is True

    run = ledger.get(claim.run_id)
    assert run.status == "completed"
    assert run.finished_at is not None
    assert run.detail == {"proposals": 2}


def test_claim_then_fail_transition(ledger):
    fd = date(2026, 7, 3)
    claim = ledger.claim(TENANT, JOB, fd)
    assert ledger.fail(claim.run_id, detail={"error": "llm down"}) is True

    run = ledger.get(claim.run_id)
    assert run.status == "failed"
    assert run.finished_at is not None
    assert run.detail == {"error": "llm down"}


def test_catch_up_missed_day_claims_once(ledger):
    """Worker down at 09:00: on start it catches up the missed fire_date exactly
    once — the claim itself is the guard, so a re-run never doubles it (AC-1)."""
    missed = date(2026, 7, 1)
    first = ledger.claim(TENANT, JOB, missed)
    catch_up_again = ledger.claim(TENANT, JOB, missed)
    assert first.is_new is True
    assert catch_up_again.is_new is False


def test_crash_after_claim_is_stale_then_redriven(ledger):
    """A claim with no completion is a crash-mid-scan: it surfaces as stale
    (distinct from never-claimed catch-up), and re-driving it to completed clears
    it — the fire_date was never silently consumed (AC-9)."""
    fd = date(2026, 7, 3)
    claim = ledger.claim(TENANT, JOB, fd)
    # Backdate the claim to a fixed time so staleness is wall-clock-independent.
    import psycopg

    with psycopg.connect(ledger._conninfo, autocommit=True) as conn:
        conn.execute(
            "UPDATE scheduled_job_runs SET claimed_at = %s WHERE id = %s",
            (datetime(2026, 7, 3, 9, 5, tzinfo=timezone.utc), claim.run_id),
        )

    # Two hours later, still 'claimed' => stale.
    later = datetime(2026, 7, 3, 11, 0, tzinfo=timezone.utc)
    stale = ledger.stale_claimed(now=later, stale_after=timedelta(hours=1))
    assert [r.id for r in stale] == [claim.run_id]

    # Re-drive to completion; it must no longer be stale.
    assert ledger.complete(claim.run_id, detail={"redriven": True}) is True
    assert ledger.stale_claimed(now=later, stale_after=timedelta(hours=1)) == []


def test_completed_run_is_never_stale(ledger):
    fd = date(2026, 7, 3)
    claim = ledger.claim(TENANT, JOB, fd)
    ledger.complete(claim.run_id)
    later = datetime(2026, 7, 4, 0, 0, tzinfo=timezone.utc)
    assert ledger.stale_claimed(now=later, stale_after=timedelta(hours=1)) == []


def test_complete_only_transitions_a_claimed_row(ledger):
    """Idempotent transition guard: completing an already-final run is a no-op
    (returns False), so a re-drive race can't flip failed<->completed."""
    fd = date(2026, 7, 3)
    claim = ledger.claim(TENANT, JOB, fd)
    assert ledger.complete(claim.run_id) is True
    assert ledger.complete(claim.run_id) is False  # already completed
    assert ledger.fail(claim.run_id) is False  # cannot un-complete
