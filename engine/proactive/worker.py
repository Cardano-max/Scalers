"""The proactive scanner's supervised worker tick (CustomerAcq-fr1.1 AC-1 + AC-9).

A single in-process worker (no new scheduler dependency; DBOS/APScheduler are
deferred behind the blueprint's decision gate). Each tick, for one tenant:

  1. RE-DRIVE crash-mid-scan runs first — any ledger row still 'claimed' past the
     stale threshold is a scan that claimed a fire_date then died before finishing;
     re-run it and transition it (AC-9). A crash never silently consumes a fire_date.
  2. Run every DUE fire_date (today + a bounded startup catch-up), evaluated in the
     tenant's local timezone, CLAIM-then-run-then-transition. The ledger's UNIQUE
     claim makes a double-fire idempotent — catch-up fills forward exactly once (AC-1).

``scan_fn(tenant_id, fire_date) -> detail`` does the actual detect+stage-HELD work
(the orchestrator); the worker only owns scheduling + the exactly-once ledger dance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

from proactive.schedule import CronSchedule, due_fire_dates
from proactive.schedule_ledger import STATUS_CLAIMED, ScheduleLedger

ScanFn = Callable[[str, date], dict[str, Any]]

DEFAULT_JOB_ID = "daily_scan"
DEFAULT_STALE_AFTER = timedelta(hours=1)


@dataclass
class TickReport:
    ran: list[date] = field(default_factory=list)  # fire_dates newly scanned
    redriven: list[str] = field(default_factory=list)  # run ids recovered after crash
    skipped_already_done: list[date] = field(default_factory=list)
    in_flight: list[date] = field(default_factory=list)  # claimed elsewhere, not stale
    failed: list[date] = field(default_factory=list)  # scan raised -> ledger 'failed'


def run_due_scans(
    *,
    tenant_id: str,
    tz: str,
    schedule: CronSchedule,
    ledger: ScheduleLedger,
    scan_fn: ScanFn,
    now_utc: datetime,
    job_id: str = DEFAULT_JOB_ID,
    catch_up_days: int = 1,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
) -> TickReport:
    """Run one worker tick for ``tenant_id`` as of ``now_utc``."""
    report = TickReport()

    # 1) Crash recovery: re-drive stale 'claimed' runs before taking new work (AC-9).
    for run in ledger.stale_claimed(
        now=now_utc, stale_after=stale_after, tenant_id=tenant_id, job_id=job_id
    ):
        if _run_scan(ledger, scan_fn, tenant_id, run.fire_date, run.id):
            report.redriven.append(run.id)
        else:
            report.failed.append(run.fire_date)

    # 2) Due fire_dates (today + bounded catch-up), oldest first (AC-1).
    for fd in due_fire_dates(
        now_utc=now_utc, tz=tz, schedule=schedule, catch_up_days=catch_up_days
    ):
        claim = ledger.claim(tenant_id, job_id, fd)
        if not claim.is_new:
            if claim.status == STATUS_CLAIMED:
                report.in_flight.append(fd)  # another worker holds it; leave for stale
            else:
                report.skipped_already_done.append(fd)
            continue
        if _run_scan(ledger, scan_fn, tenant_id, fd, claim.run_id):
            report.ran.append(fd)
        else:
            report.failed.append(fd)

    return report


def _run_scan(
    ledger: ScheduleLedger,
    scan_fn: ScanFn,
    tenant_id: str,
    fire_date: date,
    run_id: str,
) -> bool:
    """Execute ``scan_fn`` for a claimed run and transition the ledger row. Returns
    True on completion, False if the scan raised (row marked 'failed')."""
    try:
        detail = scan_fn(tenant_id, fire_date)
    except Exception as exc:  # noqa: BLE001 — surface as a failed run, never crash the loop
        ledger.fail(run_id, detail={"error": repr(exc)})
        return False
    ledger.complete(run_id, detail=detail)
    return True
