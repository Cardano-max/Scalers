"""``scheduled_job_runs`` — the exactly-once-per-day CLAIM-THEN-TRANSITION ledger
for the proactive daily scanner (CustomerAcq-fr1.1 AC-1 + AC-9).

Why claim-THEN-transition (not claim-only). Claiming a fire_date with
``INSERT ... ON CONFLICT DO NOTHING`` (UNIQUE(tenant_id, job_id, fire_date)) makes a
double-fire idempotent — exactly one worker owns the day (AC-1). But a claim-ONLY
ledger fails SILENT for liveness: a crash after the claim but before the scan runs
leaves the fire_date "consumed" with nothing done. So the claimed row carries a
STATUS (claimed -> completed/failed); a restart detects a still-``claimed`` row
(:meth:`ScheduleLedger.stale_claimed`) and re-drives or surfaces it — the fire_date
is never silently consumed (AC-9). This is distinct from catch-up (a fire_date that
was NEVER claimed), which the claim itself guards.

Connection conventions mirror :mod:`actions.store` (autocommit, ``dict_row``, lazy
``psycopg``). DDL is ``infra/initdb/19-scheduled-job-runs.sql``.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"
_INITDB = Path(__file__).resolve().parents[2] / "infra" / "initdb"
_LEDGER_SQL = _INITDB / "19-scheduled-job-runs.sql"

# Valid job-run states. 'claimed' is transient (a run in flight); the terminals are
# 'completed' and 'failed'. A row is NEVER updated back out of a terminal state.
STATUS_CLAIMED = "claimed"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class ClaimResult:
    """Outcome of a claim attempt.

    ``is_new`` is True iff THIS caller won the race and owns the fire_date (the
    INSERT took). When False, another worker already claimed it; ``run_id`` still
    resolves to the one real run and ``status`` is its current state.
    """

    run_id: str
    is_new: bool
    status: str


@dataclass(frozen=True)
class ScheduledRun:
    id: str
    tenant_id: str
    job_id: str
    fire_date: date
    status: str
    claimed_at: datetime
    finished_at: datetime | None
    detail: dict[str, Any] | None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "ScheduledRun":
        return cls(
            id=row["id"],
            tenant_id=row["tenant_id"],
            job_id=row["job_id"],
            fire_date=row["fire_date"],
            status=row["status"],
            claimed_at=row["claimed_at"],
            finished_at=row.get("finished_at"),
            detail=row.get("detail"),
        )


def _dsn() -> str:
    return os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


class ScheduleLedger:
    """Postgres-backed claim ledger for scheduled job runs."""

    def __init__(self, conninfo: str | None = None) -> None:
        self._conninfo = conninfo or _dsn()

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self._conninfo, autocommit=True, row_factory=dict_row)

    def ensure_schema(self) -> None:
        """Apply ``19-scheduled-job-runs.sql`` (idempotent ``CREATE TABLE IF NOT EXISTS``)."""
        with self._connect() as conn:
            conn.execute(_LEDGER_SQL.read_text(encoding="utf-8"))

    def claim(self, tenant_id: str, job_id: str, fire_date: date) -> ClaimResult:
        """Claim ``fire_date`` for ``(tenant_id, job_id)``. Exactly one caller wins.

        INSERT ... ON CONFLICT DO NOTHING RETURNING id — a returned row means the
        insert took (``is_new=True``, status 'claimed'); ``None`` means the day was
        already claimed, so we re-select the existing run (``is_new=False``).
        """
        run_id = f"sjr_{uuid.uuid4().hex[:16]}"
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO scheduled_job_runs (id, tenant_id, job_id, fire_date, status)
                VALUES (%s, %s, %s, %s, 'claimed')
                ON CONFLICT (tenant_id, job_id, fire_date) DO NOTHING
                RETURNING id
                """,
                (run_id, tenant_id, job_id, fire_date),
            ).fetchone()
            if row is not None:
                return ClaimResult(run_id=row["id"], is_new=True, status=STATUS_CLAIMED)
            existing = conn.execute(
                """
                SELECT id, status FROM scheduled_job_runs
                WHERE tenant_id = %s AND job_id = %s AND fire_date = %s
                """,
                (tenant_id, job_id, fire_date),
            ).fetchone()
            return ClaimResult(
                run_id=existing["id"], is_new=False, status=existing["status"]
            )

    def complete(self, run_id: str, detail: dict[str, Any] | None = None) -> bool:
        """Transition a CLAIMED run to 'completed'. Returns False if it was not
        'claimed' (already terminal) — the guard makes re-drives idempotent."""
        return self._transition(run_id, STATUS_COMPLETED, detail)

    def fail(self, run_id: str, detail: dict[str, Any] | None = None) -> bool:
        """Transition a CLAIMED run to 'failed'. Returns False if already terminal."""
        return self._transition(run_id, STATUS_FAILED, detail)

    def _transition(
        self, run_id: str, status: str, detail: dict[str, Any] | None
    ) -> bool:
        from psycopg.types.json import Json

        with self._connect() as conn:
            row = conn.execute(
                """
                UPDATE scheduled_job_runs
                   SET status = %s, finished_at = now(), detail = %s
                 WHERE id = %s AND status = 'claimed'
                RETURNING id
                """,
                (status, Json(detail) if detail is not None else None, run_id),
            ).fetchone()
            return row is not None

    def stale_claimed(
        self,
        *,
        now: datetime,
        stale_after: timedelta,
        tenant_id: str | None = None,
        job_id: str | None = None,
    ) -> list[ScheduledRun]:
        """Runs still 'claimed' whose claim is older than ``stale_after`` as of
        ``now`` — i.e. crash-mid-scan runs a restart must re-drive or surface.
        A completed/failed run is never returned."""
        cutoff = now - stale_after
        clauses = ["status = 'claimed'", "claimed_at <= %s"]
        params: list[Any] = [cutoff]
        if tenant_id is not None:
            clauses.append("tenant_id = %s")
            params.append(tenant_id)
        if job_id is not None:
            clauses.append("job_id = %s")
            params.append(job_id)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM scheduled_job_runs WHERE {' AND '.join(clauses)} "
                "ORDER BY claimed_at",
                params,
            ).fetchall()
        return [ScheduledRun.from_row(r) for r in rows]

    def get(self, run_id: str) -> ScheduledRun | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_job_runs WHERE id = %s", (run_id,)
            ).fetchone()
        return ScheduledRun.from_row(row) if row is not None else None

    def list_runs(self, tenant_id: str, job_id: str | None = None) -> list[ScheduledRun]:
        clauses = ["tenant_id = %s"]
        params: list[Any] = [tenant_id]
        if job_id is not None:
            clauses.append("job_id = %s")
            params.append(job_id)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM scheduled_job_runs WHERE {' AND '.join(clauses)} "
                "ORDER BY fire_date",
                params,
            ).fetchall()
        return [ScheduledRun.from_row(r) for r in rows]
