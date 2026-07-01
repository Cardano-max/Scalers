"""Durable long-horizon HITL run foundation (frontier blueprint #6 / P3).

The primitive that later makes a campaign run **durable + pausable**: a
Postgres-backed checkpointer plus an ``interrupt()`` / ``resume(Command(...))``
pair so a long-horizon run can pause at an *exact* point, persist its full state
to Postgres, and resume later — with operator input — **after a real process
restart**, WITHOUT re-firing any side-effect that already completed (the
"eureka" mid-run pause-ask-resume).

This is a STANDALONE foundation. It is **not** wired into the live campaign run
loop (``studio/agui.py`` ``_execute_provided_leads_sync``) yet — the wiring plan
lives in ``docs/design/p3-durable-hitl.md``. Nothing here sends; HELD /
approve-first semantics are preserved by construction (every side-effect stays
whatever the caller makes it — this module only guarantees it fires *at most /
exactly once* across pauses and restarts).

Design (honest framing — this is OUR OWN logic, not a vendored framework):
    The live campaign loop is an *imperative* per-lead ``for`` loop, not a
    graph, so a full graph engine (LangGraph ``AsyncPostgresSaver`` +
    ``interrupt``/``Command`` — already used for the harness graph in
    ``harness/graph.py``) is the heavier fit. We implement the same **semantics**
    it documents, on ``psycopg`` directly (a core dependency), so this runs
    under the standard test venv with no extra:

      * ``interrupt(payload)`` — pause at an exact point. First time it is
        reached it persists the full checkpoint (``status='interrupted'`` + the
        question for the operator) and raises :class:`DurableInterrupt`. On a
        later replay it returns the operator's answer instead of raising.
      * ``resume(Command(resume=value), fn)`` — feed the operator's answer in
        and re-drive ``fn`` from the top. Completed steps are ledger no-ops, so
        execution fast-forwards to exactly where it paused and continues.
      * ``step(step_key, fn)`` — the exactly-once wrapper. It claims
        ``(run_id, step_key)`` in ``durable_step_ledger``
        (``ON CONFLICT DO NOTHING``, the HARN-04 boundary pattern) and runs
        ``fn`` in the SAME transaction, so the effect and its ledger record
        commit atomically. On replay/restart an already-claimed step returns its
        recorded result and does **not** run ``fn`` again.

    Re-driving from the top mirrors LangGraph's documented resume model ("the
    node restarts from the beginning — all code before ``interrupt()`` re-runs")
    and Temporal's deterministic replay; the ledger is what makes that replay
    safe, exactly as Temporal makes activities idempotent and Google ADK
    checkpoints after every tool step. See the design doc for the citations and
    the crash-window analysis.

The durable substrate is swappable behind the same seam ``harness/runstore.py``
already documents (LangGraph checkpointer / DBOS): callers depend on the
``interrupt``/``resume``/``step`` surface, not on Postgres specifically.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import psycopg
from psycopg.rows import dict_row

__all__ = [
    "Command",
    "DurableInterrupt",
    "DurableResumeError",
    "RunAlreadyCompletedError",
    "RunNotFoundError",
    "RunOutcome",
    "DurableRun",
    "ensure_schema",
    "default_dsn",
]

# Same default DSN chain the rest of the engine uses (actions/store.py,
# tests/conftest.py): a real exported var wins, else the local docker Postgres.
_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

# Sentinel: distinguishes "don't touch result" from "set result to None" in
# ``_persist`` (a completed run with a genuine None result must still be stored).
_MISSING = object()


def default_dsn(dsn: str | None = None) -> str:
    """Resolve the DSN: explicit arg > ``ENGINE_DATABASE_URL`` > local default."""
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


# --------------------------------------------------------------------------- #
# Schema — two idempotent tables. Kept local to this module (like runstore's
# inline DDL) rather than a new initdb migration: this is a standalone
# foundation, and the DDL is CREATE ... IF NOT EXISTS so it is safe to run on a
# live cluster.
# --------------------------------------------------------------------------- #

_SCHEMA_SQL = """
-- One row per durable run: the full-state snapshot + pause marker (HARN-03 kin).
CREATE TABLE IF NOT EXISTS durable_run_checkpoint (
    run_id      TEXT PRIMARY KEY,
    tenant_id   TEXT        NOT NULL,
    status      TEXT        NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'interrupted', 'completed', 'failed')),
    cursor      INTEGER     NOT NULL DEFAULT 0,          -- monotonic progress marker
    state       JSONB       NOT NULL DEFAULT '{}'::jsonb, -- full run state snapshot
    interrupt   JSONB,                                    -- pending question for the operator; NULL unless paused
    resumes     JSONB       NOT NULL DEFAULT '{}'::jsonb, -- interrupt-ordinal -> operator answer (durable)
    result      JSONB,                                    -- final run result, set at completion
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS durable_run_checkpoint_tenant_idx
    ON durable_run_checkpoint (tenant_id);

-- The exactly-once ledger: a claimed (run_id, step_key) means the step ran; a
-- replay/restart that re-reaches it skips the side-effect (HARN-04 boundary).
CREATE TABLE IF NOT EXISTS durable_step_ledger (
    id         bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id     TEXT        NOT NULL,
    step_key   TEXT        NOT NULL,
    result     JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT durable_step_ledger_uniq UNIQUE (run_id, step_key)
);
"""


def ensure_schema(dsn: str | None = None) -> None:
    """Apply the checkpoint + step-ledger schema (idempotent DDL)."""
    with _connect(dsn) as conn:
        conn.execute(_SCHEMA_SQL)


def _connect(dsn: str | None = None, *, autocommit: bool = True):
    return psycopg.connect(default_dsn(dsn), autocommit=autocommit, row_factory=dict_row)


# --------------------------------------------------------------------------- #
# Control types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Command:
    """Resume envelope — mirrors ``langgraph.types.Command(resume=...)``.

    Carries the operator's answer back into the paused ``interrupt()`` call.
    """

    resume: Any = None


class DurableInterrupt(Exception):
    """Raised by :meth:`DurableRun.interrupt` to pause the run at an exact point.

    Carries the ``payload`` (the question surfaced to the operator) and the
    interrupt ``index`` (its ordinal within the run). The driver catches it and
    returns an :class:`RunOutcome` with ``status='interrupted'``; the checkpoint
    row is already persisted by the time it propagates.
    """

    def __init__(self, run_id: str, payload: Any, *, index: int) -> None:
        super().__init__(f"run {run_id} interrupted at #{index}")
        self.run_id = run_id
        self.payload = payload
        self.index = index


class DurableResumeError(RuntimeError):
    """Raised when :meth:`DurableRun.resume` is called on a run that is not paused."""


class RunAlreadyCompletedError(RuntimeError):
    """Raised when :meth:`DurableRun.run` targets a ``run_id`` already at completion.

    Re-running a completed run would replay its steps; the guard rejects it
    (the fk5 replay-guard analogue — see ``harness/graph.py``). Use a fresh
    ``run_id`` per run.
    """


class RunNotFoundError(RuntimeError):
    """Raised when :meth:`DurableRun.load` finds no checkpoint for ``run_id``."""


@dataclass
class RunOutcome:
    """The result of a drive: either paused at an interrupt, or completed."""

    run_id: str
    status: str  # 'interrupted' | 'completed'
    interrupt: Any = None          # the operator question, when status='interrupted'
    interrupt_index: int | None = None
    result: Any = None             # fn's return value, when status='completed'
    state: dict[str, Any] = field(default_factory=dict)

    @property
    def interrupted(self) -> bool:
        return self.status == "interrupted"

    @property
    def completed(self) -> bool:
        return self.status == "completed"


# --------------------------------------------------------------------------- #
# The durable run handle
# --------------------------------------------------------------------------- #

RunFn = Callable[["DurableRun"], Any]


class DurableRun:
    """A durable, pausable run keyed by ``run_id``.

    Lifecycle:
        run = DurableRun(run_id, tenant_id, dsn=dsn); run.ensure_schema()
        outcome = run.run(fn)                 # drives fn(run) to first pause/end
        ...                                   # process may exit here
        run2 = DurableRun.load(run_id, dsn)   # fresh object, fresh connection
        outcome = run2.resume(Command(resume=answer), fn)

    ``fn`` is the run body. It receives this handle and uses ``step()`` for each
    side-effecting unit and ``interrupt()`` at each pause point. It must be a
    deterministic replay of the same control flow given the same inputs — the
    ledger makes completed steps no-ops, so re-driving is safe and cheap.
    """

    def __init__(self, run_id: str, tenant_id: str, *, dsn: str | None = None) -> None:
        self.run_id = run_id
        self.tenant_id = tenant_id
        self._dsn = default_dsn(dsn)
        self.state: dict[str, Any] = {}
        self.cursor: int = 0
        self._resumes: dict[str, Any] = {}   # str(ordinal) -> operator answer
        self._interrupt_i: int = 0           # per-drive interrupt counter
        self._status: str = "running"

    # ---- construction / rehydration ------------------------------------- #

    def ensure_schema(self) -> None:
        ensure_schema(self._dsn)

    @classmethod
    def load(cls, run_id: str, dsn: str | None = None) -> "DurableRun":
        """Rehydrate a run from its persisted checkpoint (the restart path).

        A *fresh* object over a *fresh* connection — this is what a new process
        does after a restart. Raises :class:`RunNotFoundError` if the run was
        never checkpointed.
        """
        row = _fetch_row(run_id, dsn)
        if row is None:
            raise RunNotFoundError(run_id)
        run = cls(run_id, row["tenant_id"], dsn=dsn)
        run.state = row["state"] or {}
        run.cursor = row["cursor"] or 0
        run._resumes = row["resumes"] or {}
        run._status = row["status"]
        return run

    # ---- exactly-once side-effect wrapper ------------------------------- #

    def step(self, step_key: str, fn: Callable[[psycopg.Connection], Any]) -> Any:
        """Run ``fn`` **exactly once** for this run, keyed by ``step_key``.

        Claims ``(run_id, step_key)`` in the ledger and runs ``fn(conn)`` in the
        SAME transaction, so the side-effect (any writes ``fn`` makes on ``conn``)
        and its ledger record commit atomically — a crash before commit leaves
        neither, so the step is retried cleanly. If the step was already claimed
        (a replay after a pause/restart), ``fn`` is NOT run and the recorded
        result is returned: a completed send/draft never re-fires.

        ``fn`` receives the live connection: any writes it makes **on ``conn``**
        join the atomic claim — this is the guarantee the tests exercise, and it
        is exactly-once for DB-visible effects written on ``conn``. Two things it
        is deliberately NOT:

        * A call that opens its OWN connection — e.g. the campaign loop's
          ``record_pending_action`` (``actions/store.py`` opens an autocommit
          connection; it takes a ``dsn``, not a caller ``conn``) — is *not* bound
          into this transaction. Used with such a call, ``step()`` is a
          REPLAY-SKIP marker (don't re-run the orchestration on resume) and that
          call's own idempotency key owns its exactly-once. See the design doc
          §5.1 for the layering.
        * A truly *external* effect (a real email send) must use the two-phase
          outbox instead (``sideeffects/`` + the design doc) — a rolled-back
          transaction cannot un-send a network call.
        """
        result_key = f"__result__:{step_key}"
        with _connect(self._dsn, autocommit=False) as conn:
            claimed = conn.execute(
                "INSERT INTO durable_step_ledger (run_id, step_key) VALUES (%s, %s) "
                "ON CONFLICT (run_id, step_key) DO NOTHING RETURNING id",
                (self.run_id, step_key),
            ).fetchone()
            if claimed is None:
                # Already ran — return the recorded result, do NOT re-fire.
                conn.rollback()
                return self._prior_step_result(step_key)
            result = fn(conn)  # side-effect writes ride this transaction
            conn.execute(
                "UPDATE durable_step_ledger SET result = %s::jsonb "
                "WHERE run_id = %s AND step_key = %s",
                (json.dumps(_jsonable(result)), self.run_id, step_key),
            )
            conn.commit()
        # Cache the result on the state snapshot too, so an observer/UI sees it.
        self.state[result_key] = _jsonable(result)
        return result

    def _prior_step_result(self, step_key: str) -> Any:
        with _connect(self._dsn) as conn:
            row = conn.execute(
                "SELECT result FROM durable_step_ledger WHERE run_id = %s AND step_key = %s",
                (self.run_id, step_key),
            ).fetchone()
        return row["result"] if row else None

    def has_run_step(self, step_key: str) -> bool:
        """True if ``step_key`` is already in the ledger for this run."""
        with _connect(self._dsn) as conn:
            row = conn.execute(
                "SELECT 1 FROM durable_step_ledger WHERE run_id = %s AND step_key = %s",
                (self.run_id, step_key),
            ).fetchone()
        return row is not None

    # ---- interrupt / pause point ---------------------------------------- #

    def interrupt(self, payload: Any) -> Any:
        """Pause here and ask the operator ``payload``; return their answer on resume.

        First reach: persist the full checkpoint (``status='interrupted'`` + the
        question) and raise :class:`DurableInterrupt`. On a later replay (after
        :meth:`resume` supplied an answer for this ordinal) it returns that
        answer instead of raising, and control flows past the pause.
        """
        index = self._interrupt_i
        self._interrupt_i += 1
        key = str(index)
        if key in self._resumes:
            return self._resumes[key]  # already answered — replaying past the pause
        # New pause point: persist state + the question, then stop the drive.
        self._persist(status="interrupted", interrupt={"index": index, "payload": _jsonable(payload)})
        raise DurableInterrupt(self.run_id, payload, index=index)

    # ---- state snapshot -------------------------------------------------- #

    def set_state(self, **updates: Any) -> None:
        """Merge ``updates`` into the in-memory state (persisted at next checkpoint)."""
        self.state.update(updates)

    def checkpoint(self, *, cursor: int | None = None) -> None:
        """Persist the current state snapshot (and optionally advance ``cursor``)."""
        if cursor is not None:
            self.cursor = cursor
        self._persist(status="running", interrupt=None)

    # ---- drivers --------------------------------------------------------- #

    def run(self, fn: RunFn) -> RunOutcome:
        """Start a fresh run and drive ``fn`` to its first pause or completion.

        Rejects a ``run_id`` that already reached completion
        (:class:`RunAlreadyCompletedError`). A run that is mid-flight or paused
        is continued via :meth:`resume` (or :meth:`load` then :meth:`resume`),
        not restarted.
        """
        existing = _fetch_row(self.run_id, self._dsn)
        if existing is not None:
            if existing["status"] == "completed":
                raise RunAlreadyCompletedError(self.run_id)
            if existing["status"] == "interrupted":
                raise DurableResumeError(
                    f"run {self.run_id} is paused — continue with resume(), not run()"
                )
            # 'running' / 'failed': a drive started but never paused or finished —
            # a crash mid-step. Re-drive from the top; the ledger skips completed
            # steps, so recovery finishes exactly once (harness.recover analogue).
            self._reload()
        else:
            self._persist(status="running", interrupt=None)
        return self._drive(fn)

    def resume(self, command: Command, fn: RunFn) -> RunOutcome:
        """Feed the operator's answer in and re-drive ``fn`` from the top.

        The run must be paused (``status='interrupted'``). The answer is recorded
        durably against the pending interrupt ordinal, so it survives a crash
        *during* the resume itself; then ``fn`` is re-driven — completed
        ``step()`` calls are ledger no-ops and the pending ``interrupt()`` now
        returns ``command.resume``, so control fast-forwards past the pause.
        """
        self._reload()
        if self._status != "interrupted":
            raise DurableResumeError(
                f"run {self.run_id} is '{self._status}', not paused — nothing to resume"
            )
        pending = self._pending_interrupt_index()
        self._resumes[str(pending)] = _jsonable(command.resume)
        self._persist(status="running", interrupt=None)
        return self._drive(fn)

    def _drive(self, fn: RunFn) -> RunOutcome:
        """Invoke ``fn(self)`` once; translate a pause/exit into a :class:`RunOutcome`."""
        self._interrupt_i = 0  # ordinals restart each drive; the resumes map is the memory
        try:
            result = fn(self)
        except DurableInterrupt as di:
            # Checkpoint already persisted inside interrupt().
            return RunOutcome(
                run_id=self.run_id,
                status="interrupted",
                interrupt=di.payload,
                interrupt_index=di.index,
                state=dict(self.state),
            )
        self._status = "completed"
        self._persist(status="completed", interrupt=None, result=result)
        return RunOutcome(
            run_id=self.run_id, status="completed", result=result, state=dict(self.state)
        )

    # ---- persistence ----------------------------------------------------- #

    def _persist(self, *, status: str, interrupt: Any, result: Any = _MISSING) -> None:
        """Upsert the checkpoint row with the current state/cursor/resumes."""
        self._status = status
        set_result = result is not _MISSING
        with _connect(self._dsn) as conn:
            conn.execute(
                """
                INSERT INTO durable_run_checkpoint
                    (run_id, tenant_id, status, cursor, state, interrupt, resumes, result)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)
                ON CONFLICT (run_id) DO UPDATE SET
                    status    = EXCLUDED.status,
                    cursor    = EXCLUDED.cursor,
                    state     = EXCLUDED.state,
                    interrupt = EXCLUDED.interrupt,
                    resumes   = EXCLUDED.resumes,
                    result    = CASE WHEN %s THEN EXCLUDED.result
                                     ELSE durable_run_checkpoint.result END,
                    updated_at = now()
                """,
                (
                    self.run_id, self.tenant_id, status, self.cursor,
                    json.dumps(_jsonable(self.state)),
                    json.dumps(_jsonable(interrupt)) if interrupt is not None else None,
                    json.dumps(self._resumes),
                    json.dumps(_jsonable(result)) if set_result else None,
                    set_result,
                ),
            )

    def _reload(self) -> None:
        """Refresh in-memory state/status/resumes from the durable row."""
        row = _fetch_row(self.run_id, self._dsn)
        if row is None:
            raise RunNotFoundError(self.run_id)
        self.state = row["state"] or {}
        self.cursor = row["cursor"] or 0
        self._resumes = row["resumes"] or {}
        self._status = row["status"]
        self._pending = row["interrupt"]

    def _pending_interrupt_index(self) -> int:
        row = _fetch_row(self.run_id, self._dsn)
        pend = row["interrupt"] if row else None
        if pend and "index" in pend:
            return int(pend["index"])
        # No explicit marker (defensive): the next unanswered ordinal.
        return len(self._resumes)

    # ---- read helpers ---------------------------------------------------- #

    @property
    def status(self) -> str:
        return self._status

    def snapshot(self) -> dict[str, Any] | None:
        """The persisted checkpoint row (the durable session record), or None."""
        return _fetch_row(self.run_id, self._dsn)


def _fetch_row(run_id: str, dsn: str | None) -> dict[str, Any] | None:
    with _connect(dsn) as conn:
        return conn.execute(
            "SELECT * FROM durable_run_checkpoint WHERE run_id = %s", (run_id,)
        ).fetchone()


# --------------------------------------------------------------------------- #
# JSON helpers — keep snapshots msgpack/JSONB-safe (fk5 serializer note kin).
# --------------------------------------------------------------------------- #


def _jsonable(value: Any) -> Any:
    """Best-effort convert ``value`` to a JSON-serializable form.

    Handles the shapes the run body produces (dataclasses, pydantic models,
    sets, uuids); anything else falls back to ``str`` so a snapshot never fails
    to persist. This never *invents* data — it only coerces representation.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    model_dump = getattr(value, "model_dump", None)  # pydantic v2
    if callable(model_dump):
        try:
            return _jsonable(model_dump(mode="json"))
        except TypeError:
            return _jsonable(model_dump())
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict

        return _jsonable(asdict(value))
    return str(value)
