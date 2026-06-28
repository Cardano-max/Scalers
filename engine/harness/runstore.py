"""Durable run-state / status store (HARN-03, systemdesign §5.1).

A Postgres JSONB status store with an **append-only** ``steps[]`` trajectory,
plus a query API for run history (``list_runs``) and per-run trajectory
(``get_run``). These rows are the read models the gateway serves for the console
Runs / Overview screens.

``RunStore`` is a **thin interface** on purpose: the canonical durable substrate
is the LangGraph Postgres checkpointer, but DBOS Transact is kept slot-able
behind this protocol (stack-decision.md) — a later swap changes the
implementation, not the callers. ``start_run`` enforces a **unique run_id per
run**, the durable half of the CustomerAcq-fk5 fix (the graph's replay guard is
the other half).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from .graph import CompiledGraph
from .router import route
from .state import AutonomyMode, GraphState, RouteDecision


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs-review"


class RunStep(BaseModel):
    """One append-only trajectory step."""

    model_config = {"frozen": True}

    seq: int
    at: str
    text: str
    state: str


class RunRecord(BaseModel):
    """A run row + its trajectory (the console Runs/Overview read model)."""

    run_id: str
    tenant_id: str
    type: str
    trigger: str
    status: RunStatus
    steps: list[RunStep] = Field(default_factory=list)
    auto_count: int = 0
    review_count: int = 0
    retries: int = 0


class RunExistsError(RuntimeError):
    """Raised when ``start_run`` is given a ``run_id`` that already exists.

    Run-key uniqueness prevents reusing a completed thread_id (CustomerAcq-fk5).
    """


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@runtime_checkable
class RunStore(Protocol):
    """Thin durable-run interface — DBOS-swappable (stack-decision.md)."""

    def start_run(self, run_id: str, tenant_id: str, run_type: str, trigger: str) -> None: ...

    def append_step(self, run_id: str, *, text: str, state: str) -> None: ...

    def finish_run(
        self,
        run_id: str,
        *,
        status: RunStatus,
        auto_count: int = 0,
        review_count: int = 0,
        retries: int = 0,
    ) -> None: ...

    def get_run(self, run_id: str) -> RunRecord | None: ...

    def list_runs(self, tenant_id: str) -> list[RunRecord]: ...


class InMemoryRunStore:
    """In-memory ``RunStore`` for the demo and tests."""

    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}

    def start_run(self, run_id: str, tenant_id: str, run_type: str, trigger: str) -> None:
        if run_id in self._runs:
            raise RunExistsError(run_id)
        self._runs[run_id] = RunRecord(
            run_id=run_id,
            tenant_id=tenant_id,
            type=run_type,
            trigger=trigger,
            status=RunStatus.RUNNING,
        )

    def append_step(self, run_id: str, *, text: str, state: str) -> None:
        run = self._runs[run_id]
        step = RunStep(seq=len(run.steps), at=_now(), text=text, state=state)
        # Append-only: never mutate or drop an existing step.
        run.steps.append(step)

    def finish_run(
        self,
        run_id: str,
        *,
        status: RunStatus,
        auto_count: int = 0,
        review_count: int = 0,
        retries: int = 0,
    ) -> None:
        run = self._runs[run_id]
        self._runs[run_id] = run.model_copy(
            update={
                "status": status,
                "auto_count": auto_count,
                "review_count": review_count,
                "retries": retries,
            }
        )

    def get_run(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    def list_runs(self, tenant_id: str) -> list[RunRecord]:
        return [r for r in self._runs.values() if r.tenant_id == tenant_id]


class PostgresRunStore:
    """Postgres ``RunStore``: one ``runs`` row per run, JSONB append-only ``steps``.

    The append (``steps = steps || step``) runs under the row lock of the
    ``UPDATE``, so concurrent step writers to the same run serialize correctly.
    For very large trajectories the normalized ``run_steps`` table (§5.1) is the
    scale path; Phase 1 uses the denormalized JSONB array per the bead.

    psycopg is imported lazily so the in-memory path needs no driver installed.
    """

    def __init__(self, conninfo: str) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self._connect = lambda: psycopg.connect(
            conninfo, autocommit=True, row_factory=dict_row
        )

    def setup(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id        TEXT PRIMARY KEY,
                    tenant_id     TEXT        NOT NULL,
                    type          TEXT        NOT NULL,
                    trigger       TEXT        NOT NULL,
                    status        TEXT        NOT NULL,
                    steps         JSONB       NOT NULL DEFAULT '[]'::jsonb,
                    auto_count    INTEGER     NOT NULL DEFAULT 0,
                    review_count  INTEGER     NOT NULL DEFAULT 0,
                    retries       INTEGER     NOT NULL DEFAULT 0,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS runs_tenant_idx ON runs (tenant_id);
                """
            )

    def start_run(self, run_id: str, tenant_id: str, run_type: str, trigger: str) -> None:
        import psycopg

        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO runs (run_id, tenant_id, type, trigger, status) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (run_id, tenant_id, run_type, trigger, RunStatus.RUNNING.value),
                )
        except psycopg.errors.UniqueViolation as exc:
            raise RunExistsError(run_id) from exc

    def append_step(self, run_id: str, *, text: str, state: str) -> None:
        with self._connect() as conn:
            # seq = current array length; append atomically under the row lock.
            conn.execute(
                """
                UPDATE runs
                   SET steps = steps || jsonb_build_array(
                           jsonb_build_object(
                               'seq', jsonb_array_length(steps),
                               'at', %s, 'text', %s, 'state', %s)),
                       updated_at = now()
                 WHERE run_id = %s
                """,
                (_now(), text, state, run_id),
            )

    def finish_run(
        self,
        run_id: str,
        *,
        status: RunStatus,
        auto_count: int = 0,
        review_count: int = 0,
        retries: int = 0,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE runs SET status=%s, auto_count=%s, review_count=%s, "
                "retries=%s, updated_at=now() WHERE run_id=%s",
                (status.value, auto_count, review_count, retries, run_id),
            )

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id=%s", (run_id,)
            ).fetchone()
        return self._to_record(row) if row else None

    def list_runs(self, tenant_id: str) -> list[RunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs WHERE tenant_id=%s ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
        return [self._to_record(row) for row in rows]

    @staticmethod
    def _to_record(row: dict[str, Any]) -> RunRecord:
        return RunRecord(
            run_id=row["run_id"],
            tenant_id=row["tenant_id"],
            type=row["type"],
            trigger=row["trigger"],
            status=RunStatus(row["status"]),
            steps=[RunStep(**s) for s in row["steps"]],
            auto_count=row["auto_count"],
            review_count=row["review_count"],
            retries=row["retries"],
        )


async def execute_and_record(
    graph: CompiledGraph,
    store: RunStore,
    *,
    run_id: str,
    tenant_id: str,
    run_type: str,
    trigger: str,
    init: GraphState,
    autonomy: AutonomyMode = AutonomyMode.AUTO,
) -> RunRecord:
    """Run the graph and record its trajectory + outcome to the durable store.

    The canonical durable run-driver: ``start_run`` (unique run_id) → append a
    step per node as the graph advances → ``finish_run`` with the routed
    decision's counters. This is what feeds the console Runs/Overview.
    """

    store.start_run(run_id, tenant_id, run_type, trigger)
    try:
        async for update in graph.astream(run_id, init):
            for node in update:
                store.append_step(run_id, text=f"{node} completed", state=node)
    except Exception:
        store.finish_run(run_id, status=RunStatus.FAILED)
        raise

    values = graph.get_state(run_id).values
    confidence = values.get("confidence") or 0.0
    decision = route(confidence, autonomy=autonomy)
    store.finish_run(
        run_id,
        status=RunStatus.COMPLETED,
        auto_count=1 if decision is RouteDecision.AUTO else 0,
        review_count=1 if decision is RouteDecision.REVIEW else 0,
    )
    result = store.get_run(run_id)
    assert result is not None
    return result
