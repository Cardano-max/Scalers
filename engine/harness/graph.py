"""The hand-built Harness over the LangGraph spine + durable checkpointer
(systemdesign §6.2 / §2.2 / HARN-01 / HARN-03).

``Harness`` wraps a LangGraph ``StateGraph`` behind the §6.2 control-core
interface: ``add_node`` / ``add_edge`` / ``add_conditional`` / ``compile``.
Edges are static or keyed on COMPUTED fields only — the model never chooses the
next step. ``compile`` injects a checkpointer (LangGraph's **Postgres**
checkpointer in production — durable state, crash recovery; in-memory for the
demo/tests) and returns a ``CompiledGraph``:

* ``run``     — start a fresh run, keyed by ``run_id``.
* ``recover`` — resume a *crashed* run from the last completed checkpoint.
* ``resume``  — resume a run paused at a LangGraph ``interrupt()`` (HITL).
* ``astream`` — relay per-node frames out to the thin portal.

A checkpoint is a save-point, not exactly-once execution (§2.2): re-running a
COMPLETED ``run_id`` would replay the checkpoint and make append-reduced
channels (``step_log``) accumulate (CustomerAcq-fk5). ``run`` guards against
that — replaying a completed thread is rejected; crashed/paused threads route to
``recover`` / ``resume``. Run-key uniqueness is also enforced at the durable
store (see ``runstore``).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from .config import get_settings
from .nodes import AssembleNode, ResearchNode
from .serde import make_serde
from .spans import instrument
from .state import Decision, GraphState, Node

__all__ = [
    "Harness",
    "CompiledGraph",
    "RunAlreadyCompletedError",
    "RunInProgressError",
    "make_checkpointer",
    "build_demo_graph",
    "get_graph",
    "START",
    "END",
]


class RunAlreadyCompletedError(RuntimeError):
    """Raised when ``run`` is called for a ``run_id`` already at END (fk5).

    Replaying a completed thread would re-accumulate append-reduced channels and
    skew the router. Use a fresh ``run_id`` per run.
    """


class RunInProgressError(RuntimeError):
    """Raised when ``run`` is called for a crashed/paused ``run_id``.

    The run has a checkpoint with pending work — continue it with ``recover``
    (crash) or ``resume`` (HITL interrupt), don't restart it.
    """


async def make_checkpointer() -> BaseCheckpointSaver:
    """Return the durable async Postgres checkpointer, or in-memory if no DB.

    The harness runs every graph via async ``ainvoke`` / ``astream``, so the
    Postgres path uses ``AsyncPostgresSaver`` over an ``AsyncConnectionPool`` —
    the *synchronous* ``PostgresSaver`` raises ``NotImplementedError`` under the
    async Pregel loop (``aget_tuple``). The Postgres dependency is imported
    lazily so the in-memory path needs no driver. Both checkpointers use the
    type-allow-listed serializer so durable checkpoints survive strict msgpack
    mode (CustomerAcq-fk5 secondary note).
    """

    serde = make_serde()
    database_url = get_settings().database_url
    if database_url:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(
            conninfo=database_url,
            max_size=10,
            open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        )
        await pool.open()
        checkpointer = AsyncPostgresSaver(pool, serde=serde)
        await checkpointer.setup()  # idempotent: creates the LangGraph checkpoint tables
        return checkpointer
    return InMemorySaver(serde=serde)


class CompiledGraph:
    """A durable, runnable graph (systemdesign §6.2 / §2.2)."""

    def __init__(self, graph: Any) -> None:
        self._graph = graph

    def _config(self, run_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": run_id}}

    async def run(self, run_id: str, init: GraphState) -> GraphState:
        """Start a fresh run from ``init``, checkpointing under ``run_id``.

        Rejects a ``run_id`` that already has durable state: completed threads
        raise :class:`RunAlreadyCompletedError` (fk5), in-progress threads raise
        :class:`RunInProgressError` (use ``recover`` / ``resume``).
        """

        snapshot = await self._graph.aget_state(self._config(run_id))
        if snapshot.values:
            if snapshot.next:
                raise RunInProgressError(run_id)
            raise RunAlreadyCompletedError(run_id)

        result = await self._graph.ainvoke(init, self._config(run_id))
        return GraphState.model_validate(result)

    async def recover(self, run_id: str) -> GraphState:
        """Resume a crashed run from the last completed checkpoint (§2.2).

        Completed nodes are not re-executed; only the pending node(s) re-run, so
        the run finishes exactly once.
        """

        result = await self._graph.ainvoke(None, self._config(run_id))
        return GraphState.model_validate(result)

    async def resume(self, run_id: str, decision: Decision) -> GraphState:
        """Resume a HITL-paused run with the human's ``decision``."""

        result = await self._graph.ainvoke(
            Command(resume=decision.model_dump()), self._config(run_id)
        )
        return GraphState.model_validate(result)

    async def astream(
        self, run_id: str, init: GraphState, *, stream_mode: str = "updates"
    ) -> AsyncIterator[dict]:
        """Yield per-node state updates as the run progresses.

        The thin FastAPI portal relays these straight out as SSE frames — the
        graph owns control flow, the portal only forwards events.
        """

        async for update in self._graph.astream(
            init, self._config(run_id), stream_mode=stream_mode
        ):
            yield update

    async def get_state(self, run_id: str) -> Any:
        """Return the persisted checkpoint snapshot for ``run_id``.

        Uses the async ``aget_state`` so it works with the async Postgres
        checkpointer (the sync ``get_state`` raises on ``AsyncPostgresSaver``);
        ``aget_state`` works equally on the in-memory checkpointer.
        """

        return await self._graph.aget_state(self._config(run_id))

    async def is_complete(self, run_id: str) -> bool:
        """True if ``run_id`` has a checkpoint that reached END."""

        snapshot = await self._graph.aget_state(self._config(run_id))
        return bool(snapshot.values) and not snapshot.next


class Harness:
    """Hand-built graph: nodes + static/computed edges (systemdesign §6.2).

    The topology is declared in code here; nodes cannot redirect it. The LLM
    never decides the next step — edges are static or keyed on computed fields.
    """

    def __init__(self) -> None:
        self._builder = StateGraph(GraphState)

    def add_node(self, node: Node) -> None:
        """Register a node under its ``name``, auto-instrumented for spans.

        Every node is wrapped so it emits a structured ``node`` span (OBS-01)
        with duration + I/O; the wrapper is transparent to control flow.
        """

        self._builder.add_node(node.name, instrument(node))

    def add_edge(self, src: str, dst: str) -> None:
        """Add a static edge ``src -> dst`` (use ``START`` / ``END`` sentinels)."""

        self._builder.add_edge(src, dst)

    def add_conditional(self, src: str, choose: Callable[[GraphState], str]) -> None:
        """Add a computed edge: ``choose`` maps state to the next node name.

        ``choose`` is pure code over computed fields — not a model call.
        """

        self._builder.add_conditional_edges(src, choose)

    def compile(self, checkpointer: BaseCheckpointSaver) -> CompiledGraph:
        """Compile the graph with an injected checkpointer."""

        return CompiledGraph(self._builder.compile(checkpointer=checkpointer))


def build_demo_graph(checkpointer: BaseCheckpointSaver | None = None) -> CompiledGraph:
    """Build the fixed Phase-1 demo graph: ``START -> research -> assemble -> END``."""

    harness = Harness()
    harness.add_node(ResearchNode())
    harness.add_node(AssembleNode())
    harness.add_edge(START, "research")
    harness.add_edge("research", "assemble")
    harness.add_edge("assemble", END)
    return harness.compile(checkpointer or InMemorySaver(serde=make_serde()))


_graph: CompiledGraph | None = None
_graph_lock = asyncio.Lock()


async def get_graph() -> CompiledGraph:
    """Return the process-wide demo graph with its configured checkpointer.

    Async because the durable checkpointer (``make_checkpointer``) opens an
    async connection pool and awaits ``setup()``. Cached behind a lock so a
    burst of concurrent requests builds the graph exactly once.
    """

    global _graph
    if _graph is None:
        async with _graph_lock:
            if _graph is None:
                _graph = build_demo_graph(await make_checkpointer())
    return _graph
