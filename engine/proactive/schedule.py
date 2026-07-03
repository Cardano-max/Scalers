"""Tenant-local-timezone schedule logic for the proactive scanner (CustomerAcq-fr1.1).

Pure and wall-clock-free: the worker injects ``now_utc`` so firing is deterministic
and testable. The scanner stores a 5-field cron (e.g. ``0 9 * * *``); for a proactive
DAILY scan only the minute+hour are load-bearing, so day-of-month / month /
day-of-week restrictions are fail-closed (rejected) rather than silently ignored.

``due_fire_dates`` evaluates the stored cron in the tenant's OWN timezone and returns
every fire_date from a bounded catch-up window through today whose local fire time has
already passed — oldest first, so a worker that was down at 09:00 fills the ledger
forward exactly once (the ledger's UNIQUE claim guards against doubles).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class CronSchedule:
    """The minute+hour a daily scan fires (day/month/dow are '*')."""

    minute: int
    hour: int

    def local_fire_at(self, d: date, zone: ZoneInfo) -> datetime:
        """The tenant-local datetime the scan fires on calendar date ``d``."""
        return datetime(d.year, d.month, d.day, self.hour, self.minute, tzinfo=zone)


def parse_cron(expr: str) -> CronSchedule:
    """Parse a daily 5-field cron. Rejects anything but ``m h * * *`` (fail-closed)."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"expected 5 cron fields, got {len(fields)!r}: {expr!r}")
    minute, hour, dom, month, dow = fields
    if (dom, month, dow) != ("*", "*", "*"):
        raise ValueError(
            "proactive scanner supports a DAILY schedule only "
            f"(day/month/dow must be '*'): {expr!r}"
        )
    try:
        m, h = int(minute), int(hour)
    except ValueError as exc:
        raise ValueError(f"non-integer minute/hour in cron {expr!r}") from exc
    if not (0 <= m < 60 and 0 <= h < 24):
        raise ValueError(f"minute/hour out of range in cron {expr!r}")
    return CronSchedule(minute=m, hour=h)


def due_fire_dates(
    *,
    now_utc: datetime,
    tz: str,
    schedule: CronSchedule,
    catch_up_days: int = 1,
) -> list[date]:
    """Fire dates due as of ``now_utc``, evaluated in tenant timezone ``tz``.

    Returns every date from ``today - catch_up_days`` through today whose local fire
    time is ``<= now`` (tenant-local), oldest first. ``catch_up_days=0`` means today
    only. The ledger — not this function — enforces exactly-once, so returning an
    already-run date is harmless.
    """
    if catch_up_days < 0:
        raise ValueError("catch_up_days must be >= 0")
    zone = ZoneInfo(tz)
    now_local = now_utc.astimezone(zone)
    today_local = now_local.date()
    due: list[date] = []
    d = today_local - timedelta(days=catch_up_days)
    while d <= today_local:
        if schedule.local_fire_at(d, zone) <= now_local:
            due.append(d)
        d += timedelta(days=1)
    return due
