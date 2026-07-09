"""Review-queue archive-not-delete + TTL sweep (CustomerAcq-fr1.3, AC-1/2/3).

Audit ground truth: 278 pending drafts for 20 recipients (53 byte-identical)
accreted from dev runs. This module clears that accretion HONESTLY: a stale or
dev-run pending action is moved to ``status='archived'`` WITH a ``reason`` and
an ``archived_at`` — it is NEVER deleted, and it stays queryable. The
structural valve that PREVENTS re-accretion (content-hash dedupe + per-recipient
pending cap) lives in the t90.3 suppression/exactly-once bead; this module
consumes that ledger for per-recipient frequency awareness rather than
duplicating dedupe.

The ``actions`` table is phase3-owned (08-actions.sql); on trunk this module's
migration (``17-actions-archive.sql``) is a conditional no-op, so the archive
functions are the mechanism that activates against the real queue on the
phase3 merge. Every function is exercised in tests against a fixture actions
table in a private schema.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

import psycopg
from psycopg.rows import dict_row

from suppression.ledger import recent_send_counts

__all__ = [
    "ArchiveReport",
    "archive_stale_actions",
    "ensure_archive_schema",
    "recipient_frequency_view",
    "ttl_archive_sweep",
]

_INITDB = Path(__file__).resolve().parents[2] / "infra" / "initdb"
_ARCHIVE_SQL = _INITDB / "17-actions-archive.sql"


def _dsn(dsn: str | None) -> str:
    return dsn or os.environ.get(
        "ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers"
    )


def _connect(dsn: str | None) -> psycopg.Connection:
    return psycopg.connect(_dsn(dsn), autocommit=True, row_factory=dict_row)


@dataclass(frozen=True)
class ArchiveReport:
    """Honest counts for one archive pass. ``deleted`` is ALWAYS 0 — this
    module never hard-deletes. On a dry run ``archived`` is the count that
    WOULD be archived; nothing is written."""

    scanned: int
    archived: int
    skipped: int
    reason: str
    dry_run: bool
    deleted: int = 0


def ensure_archive_schema(dsn: str | None = None) -> None:
    """Apply the conditional actions-archive migration (idempotent; a no-op
    where the ``actions`` table does not exist)."""
    with _connect(dsn) as conn:
        conn.execute(_ARCHIVE_SQL.read_text(encoding="utf-8"))


def _where(older_than: datetime, run_id_prefixes: Sequence[str] | None) -> tuple[str, list]:
    clauses = ["status = 'pending'", "created_at < %s"]
    params: list = [older_than]
    if run_id_prefixes:
        likes = " OR ".join(["run_id LIKE %s"] * len(run_id_prefixes))
        clauses.append(f"({likes})")
        params.extend(f"{p}%" for p in run_id_prefixes)
    return " AND ".join(clauses), params


def archive_stale_actions(
    *,
    dsn: str | None = None,
    older_than: datetime,
    run_id_prefixes: Sequence[str] | None = None,
    reason: str = "dev_run_cleanup",
    dry_run: bool = True,
    now: datetime | None = None,
) -> ArchiveReport:
    """Archive stale PENDING actions (``created_at < older_than``, optionally
    scoped to ``run_id`` prefixes) to ``status='archived'`` with ``reason``.

    Dry run (default) counts what WOULD be archived and writes nothing — run it
    first and eyeball the report. The real pass UPDATEs in place (never
    DELETEs), stamps ``archived_at``, and leaves every non-matching row
    untouched. Only ``pending`` rows are eligible; a sent/approved row is never
    archived out from under the operator."""
    when = now or datetime.now(timezone.utc)
    where, params = _where(older_than, run_id_prefixes)
    with _connect(dsn) as conn:
        scanned = conn.execute(
            f"SELECT count(*) AS n FROM actions WHERE {where}", params
        ).fetchone()["n"]
        if dry_run:
            return ArchiveReport(
                scanned=scanned, archived=scanned, skipped=0, reason=reason,
                dry_run=True,
            )
        updated = conn.execute(
            f"UPDATE actions SET status='archived', reason=%s, archived_at=%s"
            f" WHERE {where}",
            [reason, when, *params],
        ).rowcount
    return ArchiveReport(
        scanned=scanned, archived=updated, skipped=scanned - updated, reason=reason,
        dry_run=False,
    )


def ttl_archive_sweep(
    *,
    dsn: str | None = None,
    ttl_hours: int = 168,
    now: datetime | None = None,
) -> str:
    """Archive every pending action older than ``ttl_hours`` with
    ``reason='ttl'`` and return the daily surfaced line. This is the callable
    the proactive scanner (fr1.1) invokes on its daily tick — a stale draft is
    archived-with-reason, never silently vanished."""
    when = now or datetime.now(timezone.utc)
    report = archive_stale_actions(
        dsn=dsn, older_than=when - timedelta(hours=ttl_hours), reason="ttl",
        dry_run=False, now=when,
    )
    return (
        f"TTL auto-archive: {report.archived} pending action(s) older than "
        f"{ttl_hours}h archived (reason=ttl)"
    )


def recipient_frequency_view(
    *,
    dsn: str | None = None,
    tenant_id: str,
    targets: Sequence[str],
    channel: str = "sms",
    window_hours: int = 72,
    now: datetime | None = None,
) -> dict[str, int]:
    """Per-recipient recent-promo-send counts for a queue view, read from the
    t90.3 suppression ledger (``send_events``) — cross-ref, NOT a second
    dedupe. Every requested target appears in the result (0 when it has no
    sends), so counts are honest rather than sparse."""
    return recent_send_counts(
        tenant_id=tenant_id, identifiers=targets, channel=channel,
        window_hours=window_hours, now=now, dsn=dsn,
    )
