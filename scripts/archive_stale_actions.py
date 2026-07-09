#!/usr/bin/env python3
"""Archive stale / dev-run pending review-queue actions — NEVER delete.

One-time and repeatable cleanup for the review-queue accretion the audit flagged
(278 pending drafts for 20 recipients). Moves matching PENDING actions to
status='archived' with a reason; archived rows stay queryable. DRY-RUN by
default: it prints a counts report and writes nothing until you pass --apply.

Usage:
  # dry run (default): report what WOULD be archived, write nothing
  python scripts/archive_stale_actions.py --run-id-prefix devrun- --older-than-hours 168

  # the real pass
  python scripts/archive_stale_actions.py --run-id-prefix devrun- --older-than-hours 168 --apply

  # TTL sweep (reason='ttl'), what the daily scanner calls
  python scripts/archive_stale_actions.py --ttl-hours 168 --apply

The actions table is phase3-owned; run this against a database where it exists.
DSN comes from ENGINE_DATABASE_URL (else the local default).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent / "engine"
sys.path.insert(0, str(ENGINE_DIR))

from ops.archive import archive_stale_actions, ensure_archive_schema, ttl_archive_sweep  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Archive stale pending review-queue actions (never delete).")
    p.add_argument("--dsn", default=None, help="Postgres DSN (default: ENGINE_DATABASE_URL).")
    p.add_argument("--run-id-prefix", action="append", default=None, dest="run_id_prefixes",
                   help="Only archive pendings whose run_id starts with this (repeatable).")
    p.add_argument("--older-than-hours", type=int, default=168,
                   help="Archive pendings created more than N hours ago (default 168 = 7d).")
    p.add_argument("--reason", default="dev_run_cleanup", help="Reason stamped on archived rows.")
    p.add_argument("--ttl-hours", type=int, default=None,
                   help="Run the TTL sweep instead (reason='ttl'); overrides the flags above.")
    p.add_argument("--apply", action="store_true",
                   help="Perform the archive. Without this it is a DRY RUN (no writes).")
    args = p.parse_args(argv)

    ensure_archive_schema(args.dsn)
    now = datetime.now(timezone.utc)

    if args.ttl_hours is not None:
        if not args.apply:
            report = archive_stale_actions(
                dsn=args.dsn, older_than=now - timedelta(hours=args.ttl_hours),
                reason="ttl", dry_run=True, now=now,
            )
            print(f"[DRY RUN] TTL sweep would archive {report.archived} pending action(s) "
                  f"older than {args.ttl_hours}h (reason=ttl). Re-run with --apply.")
            return 0
        print(ttl_archive_sweep(dsn=args.dsn, ttl_hours=args.ttl_hours, now=now))
        return 0

    report = archive_stale_actions(
        dsn=args.dsn, older_than=now - timedelta(hours=args.older_than_hours),
        run_id_prefixes=args.run_id_prefixes, reason=args.reason,
        dry_run=not args.apply, now=now,
    )
    tag = "DRY RUN — no writes" if report.dry_run else "APPLIED"
    print(f"[{tag}] scanned={report.scanned} archived={report.archived} "
          f"skipped={report.skipped} deleted={report.deleted} reason={report.reason!r}")
    if report.dry_run:
        print("Re-run with --apply to perform the archive.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
