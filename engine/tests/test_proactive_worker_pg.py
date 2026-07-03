"""PG-integration tests for the proactive worker tick (CustomerAcq-fr1.1 AC-1 + AC-9)
driving the real scheduled_job_runs ledger with an injected fake scan_fn.

Requires a real local Postgres (RUN_PG_TESTS / ENGINE_DATABASE_URL).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import psycopg
import pytest

from tests.conftest import private_schema

pytestmark = pytest.mark.skipif(
    not os.getenv("ENGINE_DATABASE_URL"), reason="requires Postgres"
)

TENANT = "sdt-test"
TZ = "America/Los_Angeles"


@pytest.fixture()
def env():
    from proactive.schedule_ledger import ScheduleLedger

    with private_schema("18-scheduled-job-runs.sql") as sch:
        yield SimpleNamespace(ledger=ScheduleLedger(sch.dsn), dsn=sch.dsn)


class _CountingScan:
    """Fake scan_fn recording each (tenant, fire_date) it is asked to run."""

    def __init__(self, *, raises: bool = False):
        self.calls: list[tuple[str, date]] = []
        self.raises = raises

    def __call__(self, tenant_id: str, fire_date: date) -> dict:
        self.calls.append((tenant_id, fire_date))
        if self.raises:
            raise RuntimeError("scan blew up")
        return {"proposals": 1, "fire_date": fire_date.isoformat()}


def _tick(env, scan, now_utc, **kw):
    from proactive.schedule import parse_cron
    from proactive.worker import run_due_scans

    return run_due_scans(
        tenant_id=TENANT, tz=TZ, schedule=parse_cron("0 9 * * *"),
        ledger=env.ledger, scan_fn=scan, now_utc=now_utc, **kw,
    )


def test_first_tick_after_fire_runs_once_and_completes(env):
    scan = _CountingScan()
    now = datetime(2026, 7, 3, 17, 0, tzinfo=timezone.utc)  # 10:00 LA
    report = _tick(env, scan, now, catch_up_days=0)

    assert report.ran == [date(2026, 7, 3)]
    assert scan.calls == [(TENANT, date(2026, 7, 3))]
    runs = env.ledger.list_runs(TENANT)
    assert len(runs) == 1 and runs[0].status == "completed"
    assert runs[0].detail == {"proposals": 1, "fire_date": "2026-07-03"}


def test_double_tick_same_day_runs_scan_once(env):
    scan = _CountingScan()
    now = datetime(2026, 7, 3, 17, 0, tzinfo=timezone.utc)
    _tick(env, scan, now, catch_up_days=0)
    report2 = _tick(env, scan, now, catch_up_days=0)

    assert len(scan.calls) == 1  # the ledger claim blocks the second run
    assert report2.ran == []
    assert report2.skipped_already_done == [date(2026, 7, 3)]


def test_startup_catch_up_runs_each_missed_day_once(env):
    scan = _CountingScan()
    # Worker starts day 3 at 10:00 LA having missed days 1 and 2.
    now = datetime(2026, 7, 3, 17, 0, tzinfo=timezone.utc)
    report = _tick(env, scan, now, catch_up_days=2)
    assert report.ran == [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)]

    # A second startup tick must NOT re-run any of them.
    report2 = _tick(env, scan, now, catch_up_days=2)
    assert len(scan.calls) == 3
    assert report2.ran == []
    assert set(report2.skipped_already_done) == {
        date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)
    }


def test_crash_mid_scan_is_redriven_on_restart(env):
    """A run claimed but never finished (crash mid-scan) is re-driven on the next
    tick and transitioned to completed — the fire_date was never silently lost."""
    fd = date(2026, 7, 3)
    claim = env.ledger.claim(TENANT, "daily_scan", fd)  # claimed, never completed
    # Backdate the claim so it is stale relative to the tick's 'now'.
    with psycopg.connect(env.dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE scheduled_job_runs SET claimed_at = %s WHERE id = %s",
            (datetime(2026, 7, 3, 16, 5, tzinfo=timezone.utc), claim.run_id),
        )

    scan = _CountingScan()
    now = datetime(2026, 7, 3, 17, 30, tzinfo=timezone.utc)  # >1h after the claim
    report = _tick(env, scan, now, catch_up_days=0)

    assert report.redriven == [claim.run_id]
    assert scan.calls == [(TENANT, fd)]  # re-driven exactly once
    run = env.ledger.get(claim.run_id)
    assert run.status == "completed"
    # And it is not re-run again on the following tick.
    report2 = _tick(env, scan, now, catch_up_days=0)
    assert len(scan.calls) == 1
    assert report2.redriven == []


def test_scan_failure_marks_run_failed_not_completed(env):
    scan = _CountingScan(raises=True)
    now = datetime(2026, 7, 3, 17, 0, tzinfo=timezone.utc)
    report = _tick(env, scan, now, catch_up_days=0)

    assert report.failed == [date(2026, 7, 3)]
    assert report.ran == []
    runs = env.ledger.list_runs(TENANT)
    assert len(runs) == 1 and runs[0].status == "failed"
    assert "scan blew up" in (runs[0].detail or {}).get("error", "")


def test_fresh_claim_in_flight_is_not_redriven_before_stale(env):
    """A run claimed just now by another worker (not yet stale) is left alone, not
    re-driven — only genuinely stuck runs are recovered."""
    fd = date(2026, 7, 3)
    other = env.ledger.claim(TENANT, "daily_scan", fd)  # claimed, fresh
    # Pin claimed_at to 5 min before the tick so 'not yet stale' is deterministic.
    with psycopg.connect(env.dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE scheduled_job_runs SET claimed_at = %s WHERE id = %s",
            (datetime(2026, 7, 3, 16, 55, tzinfo=timezone.utc), other.run_id),
        )

    scan = _CountingScan()
    now = datetime(2026, 7, 3, 17, 0, tzinfo=timezone.utc)
    report = _tick(env, scan, now, catch_up_days=0, stale_after=timedelta(hours=1))

    assert scan.calls == []  # nothing re-driven, nothing newly claimed
    assert report.in_flight == [fd]
    assert env.ledger.get(other.run_id).status == "claimed"
