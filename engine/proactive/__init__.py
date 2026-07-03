"""Proactive daily scanner (CustomerAcq-fr1.1).

The scanner gives the studio "a good, honest reason to reach out today" without a
human remembering the cadence. HARD INVARIANT: nothing sends. Every scheduled run
stages HELD proposals only (the 439 HOLD registry + router HOLD->REVIEW enforce it
independently); go-live stays operator-gated (t90.4).

Provenance: the detection/HELD-record spine is reimplemented clean from the prior
unpushed slice ``feat/autonomous-orchestrator @ 5d25b4f`` (which lived only in the
frozen eng5/src repo, never merged). That slice had NO scheduler, NO
``scheduled_job_runs`` claim ledger, NO tenant-TZ worker/catch-up, NO follow-up
detector and NO degraded preflight — those are net-new here.
"""

from __future__ import annotations

from proactive.detectors import (
    ArtistSpecial,
    Opportunity,
    PriorSend,
    artist_special_opportunities,
    follow_up_opportunities,
    holiday_opportunities,
)
from proactive.orchestrator import ScanReport, run_daily_scan
from proactive.schedule import CronSchedule, due_fire_dates, parse_cron
from proactive.schedule_ledger import ClaimResult, ScheduleLedger, ScheduledRun
from proactive.worker import TickReport, run_due_scans

__all__ = [
    # scheduling + exactly-once ledger
    "ScheduleLedger", "ClaimResult", "ScheduledRun",
    "CronSchedule", "parse_cron", "due_fire_dates",
    "run_due_scans", "TickReport",
    # detection + scan
    "Opportunity", "holiday_opportunities", "PriorSend", "follow_up_opportunities",
    "ArtistSpecial", "artist_special_opportunities",
    "run_daily_scan", "ScanReport",
]
