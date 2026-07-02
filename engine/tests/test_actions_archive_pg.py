"""OPS-3 review-queue archive (fr1.3, AC-1/2/3) — PG integration.

Audit ground truth: 278 pending drafts for 20 recipients, 53 byte-identical.
This proves the hygiene VALVE: archive-with-reason (NEVER delete) for dev-run
pendings, a dry-run counts report before the real pass, a TTL auto-archive
sweep, and per-recipient frequency awareness that reads the t90.3 ledger
(cross-ref — no duplicate dedupe here).

The ``actions`` table is phase3-only; these tests build a minimal fixture
actions table in a private schema and apply the conditional archive migration
(``17-actions-archive.sql``) to it, exactly as it will run against the real
table on the phase3 merge.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from ops.archive import (
    ArchiveReport,
    archive_stale_actions,
    ensure_archive_schema,
    recipient_frequency_view,
    ttl_archive_sweep,
)
from suppression.ledger import record_send_event
from tests.conftest import private_schema

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

UTC = timezone.utc
NOW = datetime(2026, 7, 2, 19, 0, tzinfo=UTC)

# Minimal actions table mirroring the phase3 columns the archiver touches
# (id, tenant_id, run_id, target, draft, status, created_at). The status CHECK
# is named exactly as phase3's so the migration widens the real constraint.
_ACTIONS_DDL = """
CREATE TABLE actions (
    id              text PRIMARY KEY,
    tenant_id       text NOT NULL,
    run_id          text,
    target          text,
    draft           text NOT NULL,
    status          text NOT NULL DEFAULT 'pending',
    idempotency_key text UNIQUE,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT actions_status_check CHECK (
        status IN ('pending','approved','sending','sent','rejected','failed'))
);
"""


def _actions_schema():
    ctx = private_schema("02-side-effect-boundary.sql", "16-suppression-consent.sql")
    return ctx


def _make_actions(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(_ACTIONS_DDL)
    ensure_archive_schema(dsn)


def _insert(dsn, *, tenant="t", run_id="devrun-1", target="+17025550001",
            draft="hi", status="pending", created_at=None):
    aid = f"act_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(
            "INSERT INTO actions (id, tenant_id, run_id, target, draft, status,"
            " idempotency_key, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (aid, tenant, run_id, target, draft, status, aid, created_at or NOW),
        )
    return aid


def _counts(dsn):
    with psycopg.connect(dsn, autocommit=True) as c:
        total = c.execute("SELECT count(*) FROM actions").fetchone()[0]
        by = dict(
            c.execute("SELECT status, count(*) FROM actions GROUP BY status").fetchall()
        )
    return total, by


# ── migration widens the status CHECK, adds reason/archived_at ───────────────


def test_migration_adds_archived_status_and_columns():
    with _actions_schema() as s:
        _make_actions(s.dsn)
        with psycopg.connect(s.dsn, autocommit=True) as c:
            cols = {
                r[0]
                for r in c.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name='actions'"
                ).fetchall()
            }
        assert {"reason", "archived_at"} <= cols


# ── dry-run counts report writes nothing ─────────────────────────────────────


def test_dry_run_reports_counts_and_writes_nothing():
    with _actions_schema() as s:
        _make_actions(s.dsn)
        old = NOW - timedelta(days=40)
        for _ in range(5):
            _insert(s.dsn, run_id="devrun-42", created_at=old)
        _insert(s.dsn, run_id="prodrun-9", created_at=NOW)  # recent, not dev-run

        report = archive_stale_actions(
            dsn=s.dsn, older_than=NOW - timedelta(days=7),
            run_id_prefixes=["devrun-"], reason="dev_run_cleanup", dry_run=True,
        )
        assert isinstance(report, ArchiveReport)
        assert report.dry_run is True
        assert report.scanned == 5
        assert report.archived == 5  # WOULD archive
        # ...but nothing changed on disk.
        _total, by = _counts(s.dsn)
        assert by.get("archived", 0) == 0
        assert by["pending"] == 6


# ── real pass: archive with reason, ZERO deletes, archived queryable ─────────


def test_apply_archives_with_reason_zero_deletes():
    with _actions_schema() as s:
        _make_actions(s.dsn)
        old = NOW - timedelta(days=40)
        ids = [_insert(s.dsn, run_id="devrun-42", created_at=old) for _ in range(5)]
        _insert(s.dsn, run_id="prodrun-9", created_at=NOW)

        total_before, _ = _counts(s.dsn)
        report = archive_stale_actions(
            dsn=s.dsn, older_than=NOW - timedelta(days=7),
            run_id_prefixes=["devrun-"], reason="dev_run_cleanup", dry_run=False,
        )
        assert report.archived == 5
        total_after, by = _counts(s.dsn)
        assert total_after == total_before  # ZERO deletes — row counts conserved
        assert by["archived"] == 5
        assert by["pending"] == 1
        # Archived rows stay queryable and carry the reason.
        with psycopg.connect(s.dsn, autocommit=True) as c:
            reasons = c.execute(
                "SELECT DISTINCT reason FROM actions WHERE status='archived'"
            ).fetchall()
            archived_ids = {
                r[0] for r in c.execute(
                    "SELECT id FROM actions WHERE status='archived'"
                ).fetchall()
            }
        assert reasons == [("dev_run_cleanup",)]
        assert archived_ids == set(ids)


def test_run_id_prefix_scopes_archive():
    with _actions_schema() as s:
        _make_actions(s.dsn)
        old = NOW - timedelta(days=40)
        _insert(s.dsn, run_id="devrun-1", created_at=old)
        _insert(s.dsn, run_id="prodrun-1", created_at=old)  # old but NOT a dev run
        report = archive_stale_actions(
            dsn=s.dsn, older_than=NOW - timedelta(days=7),
            run_id_prefixes=["devrun-"], reason="dev_run_cleanup", dry_run=False,
        )
        assert report.archived == 1
        _total, by = _counts(s.dsn)
        assert by["pending"] == 1  # the prod run is untouched


def test_recent_pending_not_archived():
    with _actions_schema() as s:
        _make_actions(s.dsn)
        _insert(s.dsn, run_id="devrun-1", created_at=NOW - timedelta(hours=1))
        report = archive_stale_actions(
            dsn=s.dsn, older_than=NOW - timedelta(days=7),
            run_id_prefixes=["devrun-"], dry_run=False,
        )
        assert report.archived == 0


def test_only_pending_actions_archived():
    with _actions_schema() as s:
        _make_actions(s.dsn)
        old = NOW - timedelta(days=40)
        _insert(s.dsn, run_id="devrun-1", created_at=old, status="sent")
        _insert(s.dsn, run_id="devrun-1", created_at=old, status="pending")
        report = archive_stale_actions(
            dsn=s.dsn, older_than=NOW - timedelta(days=7),
            run_id_prefixes=["devrun-"], dry_run=False,
        )
        assert report.archived == 1  # the sent row is left alone


# ── TTL auto-archive sweep (AC-3) ────────────────────────────────────────────


def test_ttl_sweep_archives_past_ttl_with_reason_ttl_and_surfaced_line():
    with _actions_schema() as s:
        _make_actions(s.dsn)
        for _ in range(3):
            _insert(s.dsn, run_id="anyrun", created_at=NOW - timedelta(days=10))
        _insert(s.dsn, run_id="anyrun", created_at=NOW - timedelta(hours=1))  # fresh

        line = ttl_archive_sweep(dsn=s.dsn, ttl_hours=168, now=NOW)
        assert "3" in line and "ttl" in line.lower()
        with psycopg.connect(s.dsn, autocommit=True) as c:
            n = c.execute(
                "SELECT count(*) FROM actions WHERE status='archived' AND reason='ttl'"
            ).fetchone()[0]
        assert n == 3


# ── per-recipient frequency awareness reads the t90.3 ledger ─────────────────


def test_recipient_frequency_view_reads_ledger():
    with _actions_schema() as s:
        # Two promo sends to one target inside the window, one to another.
        for _ in range(2):
            record_send_event(
                tenant_id="t", identifier="+17025550001", channel="sms", kind="promo",
                mode="test_redirect", idempotency_key=f"k{uuid.uuid4().hex}",
                occurred_at=NOW - timedelta(hours=1), dsn=s.dsn,
            )
        record_send_event(
            tenant_id="t", identifier="+17025550002", channel="sms", kind="promo",
            mode="test_redirect", idempotency_key=f"k{uuid.uuid4().hex}",
            occurred_at=NOW - timedelta(hours=1), dsn=s.dsn,
        )
        freq = recipient_frequency_view(
            dsn=s.dsn, tenant_id="t",
            targets=["+17025550001", "+17025550002", "+17025550003"],
            channel="sms", window_hours=72, now=NOW,
        )
        assert freq["+17025550001"] == 2
        assert freq["+17025550002"] == 1
        assert freq["+17025550003"] == 0  # no sends -> honest zero, not missing
