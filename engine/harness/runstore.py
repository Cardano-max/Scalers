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

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from .graph import CompiledGraph
from .router import route
from .spans import Span, collecting
from .state import AutonomyMode, GraphState, RouteDecision

# A persisted trajectory step is a structured Span (OBS-01). The name is kept
# for back-compat; the legacy fields (seq/at/text/state) still populate.
RunStep = Span


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs-review"


class RunRecord(BaseModel):
    """A run row + its trajectory (the console Runs/Overview read model).

    ``steps`` are structured spans (OBS-01): node spans are the trajectory; each
    carries ``children`` (cell/gate/tool spans) for the reasoning trace.
    """

    run_id: str
    tenant_id: str
    type: str
    trigger: str
    status: RunStatus
    steps: list[Span] = Field(default_factory=list)
    auto_count: int = 0
    review_count: int = 0
    retries: int = 0

    @property
    def last_step(self) -> Span | None:
        """The last node span (the run's current/final node)."""

        return self.steps[-1] if self.steps else None

    @property
    def failed_step(self) -> Span | None:
        """The first failed node span, if any — makes the failed node identifiable."""

        return next((s for s in self.steps if s.status == "failed"), None)


class RunExistsError(RuntimeError):
    """Raised when ``start_run`` is given a ``run_id`` that already exists.

    Run-key uniqueness prevents reusing a completed thread_id (CustomerAcq-fk5).
    """


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _legacy_step(run_id: str, *, text: str, state: str, seq: int) -> Span:
    """Build a minimal valid Span for the legacy string-based ``append_step``."""

    import uuid

    now = _now()
    return Span(
        span_id=uuid.uuid4().hex,
        run_id=run_id,
        node=state,
        kind="node",
        start_ts=now,
        end_ts=now,
        status="ok",
        seq=seq,
        at=now,
        text=text,
        state=state,
    )


@runtime_checkable
class RunStore(Protocol):
    """Thin durable-run interface — DBOS-swappable (stack-decision.md)."""

    def start_run(self, run_id: str, tenant_id: str, run_type: str, trigger: str) -> None: ...

    def append_step(self, run_id: str, *, text: str, state: str) -> None: ...

    def append_spans(self, run_id: str, spans: list[Span]) -> None: ...

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
        # Append-only: never mutate or drop an existing step.
        run.steps.append(_legacy_step(run_id, text=text, state=state, seq=len(run.steps)))

    def append_spans(self, run_id: str, spans: list[Span]) -> None:
        run = self._runs[run_id]
        base = len(run.steps)
        for i, sp in enumerate(spans):
            run.steps.append(sp.model_copy(update={"seq": base + i}))

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
        self.append_spans(run_id, [_legacy_step(run_id, text=text, state=state, seq=0)])

    def append_spans(self, run_id: str, spans: list[Span]) -> None:
        if not spans:
            return
        import json

        with self._connect() as conn:
            # Append-only under the row lock. Serialize spans (incl. nested
            # children) to a JSON array in Python and concat — avoids the
            # jsonb_build_object IndeterminateDatatype trap and handles nesting.
            base = conn.execute(
                "SELECT jsonb_array_length(steps) AS n FROM runs WHERE run_id=%s",
                (run_id,),
            ).fetchone()["n"]
            payload = [
                sp.model_copy(update={"seq": base + i}).model_dump(mode="json")
                for i, sp in enumerate(spans)
            ]
            conn.execute(
                "UPDATE runs SET steps = steps || %s::jsonb, updated_at=now() "
                "WHERE run_id=%s",
                (json.dumps(payload), run_id),
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
    with collecting(run_id) as collector:
        try:
            async for _ in graph.astream(run_id, init):
                pass  # spans are gathered by the node instrumentation
        except Exception:
            store.append_spans(run_id, collector.spans)  # incl. the failed node
            store.finish_run(run_id, status=RunStatus.FAILED)
            _mirror(run_id, tenant_id, run_type, collector.spans)
            raise

        store.append_spans(run_id, collector.spans)
        spans = list(collector.spans)

    values = (await graph.get_state(run_id)).values
    confidence = values.get("confidence") or 0.0
    decision = route(confidence, autonomy=autonomy)
    store.finish_run(
        run_id,
        status=RunStatus.COMPLETED,
        auto_count=1 if decision is RouteDecision.AUTO else 0,
        review_count=1 if decision is RouteDecision.REVIEW else 0,
    )
    _mirror(run_id, tenant_id, run_type, spans)
    result = store.get_run(run_id)
    assert result is not None
    return result


def _mirror(run_id: str, tenant_id: str, run_type: str, spans: list[Span]) -> None:
    """Best-effort Langfuse mirror — never raises, never gates (rvy.1 ADR)."""

    try:
        from observability import mirror_run

        mirror_run(run_id, tenant_id, spans, run_type=run_type)
    except Exception:
        pass
