"""Pure unit tests for the proactive scanner's tenant-local-TZ schedule logic
(CustomerAcq-fr1.1 AC-1). No DB, no wall-clock — 'now' is injected.

The scanner stores a cron ('0 9 * * *') and must fire per TENANT-LOCAL timezone,
with a bounded startup catch-up for a fire missed while the worker was down.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest


def test_parse_daily_cron():
    from proactive.schedule import parse_cron

    s = parse_cron("0 9 * * *")
    assert (s.hour, s.minute) == (9, 0)


def test_parse_rejects_unsupported_non_daily_cron():
    from proactive.schedule import parse_cron

    # The scanner only models a daily fire; a day-of-week restriction must be an
    # explicit error, not silently ignored (fail-closed).
    with pytest.raises(ValueError):
        parse_cron("0 9 * * 1")
    with pytest.raises(ValueError):
        parse_cron("0 9 1 * *")
    with pytest.raises(ValueError):
        parse_cron("bad")


def test_not_due_before_local_fire_time():
    from proactive.schedule import due_fire_dates, parse_cron

    # 08:00 America/Los_Angeles == 15:00 UTC. Fire is 09:00 local -> not yet.
    now = datetime(2026, 7, 3, 15, 0, tzinfo=timezone.utc)
    due = due_fire_dates(
        now_utc=now, tz="America/Los_Angeles", schedule=parse_cron("0 9 * * *"),
        catch_up_days=0,
    )
    assert due == []


def test_due_after_local_fire_time():
    from proactive.schedule import due_fire_dates, parse_cron

    # 10:00 America/Los_Angeles == 17:00 UTC. Fire 09:00 local has passed.
    now = datetime(2026, 7, 3, 17, 0, tzinfo=timezone.utc)
    due = due_fire_dates(
        now_utc=now, tz="America/Los_Angeles", schedule=parse_cron("0 9 * * *"),
        catch_up_days=0,
    )
    assert due == [date(2026, 7, 3)]


def test_timezone_changes_the_fire_decision():
    from proactive.schedule import due_fire_dates, parse_cron

    # 16:30 UTC: 09:30 in LA (fired) but 08:30 in Honolulu (not yet).
    now = datetime(2026, 7, 3, 16, 30, tzinfo=timezone.utc)
    sched = parse_cron("0 9 * * *")
    la = due_fire_dates(now_utc=now, tz="America/Los_Angeles", schedule=sched, catch_up_days=0)
    hi = due_fire_dates(now_utc=now, tz="Pacific/Honolulu", schedule=sched, catch_up_days=0)
    assert la == [date(2026, 7, 3)]
    assert hi == []


def test_catch_up_returns_missed_days_oldest_first():
    from proactive.schedule import due_fire_dates, parse_cron

    # Worker starts day 3 at 10:00 local; catch_up_days=2 covers days 1..3, all of
    # whose 09:00 fires have passed. Oldest first so the ledger fills forward.
    now = datetime(2026, 7, 3, 17, 0, tzinfo=timezone.utc)  # 10:00 LA
    due = due_fire_dates(
        now_utc=now, tz="America/Los_Angeles", schedule=parse_cron("0 9 * * *"),
        catch_up_days=2,
    )
    assert due == [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)]


def test_catch_up_excludes_today_when_fire_not_yet_reached():
    from proactive.schedule import due_fire_dates, parse_cron

    # 08:00 LA on day 3: yesterday's fire passed, today's has not.
    now = datetime(2026, 7, 3, 15, 0, tzinfo=timezone.utc)
    due = due_fire_dates(
        now_utc=now, tz="America/Los_Angeles", schedule=parse_cron("0 9 * * *"),
        catch_up_days=2,
    )
    assert due == [date(2026, 7, 1), date(2026, 7, 2)]
