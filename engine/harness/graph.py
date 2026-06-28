"""The hand-built Harness over the LangGraph spine (systemdesign §6.2 / HARN-01).

``Harness`` wraps a LangGraph ``StateGraph`` behind the §6.2 control-core
interface: ``add_node`` / ``add_edge`` / ``add_conditional`` / ``compile``.
Edges are static or keyed on COMPUTED fields only — the model never chooses the
next step. ``compile`` injects a checkpointer (LangGraph's Postgres
checkpointer in production; in-memory for the demo/tests) and returns a durable
``CompiledGraph`` whose ``run`` / ``resume`` drive and continue a run — ``resume``
continues a graph paused at a LangGraph ``interrupt()`` for human-in-the-loop.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from .config import get_settings
from .nodes import AssembleNode, ResearchNode
from .state import Decision, GraphState, Node

__all__ = ["Harness", "CompiledGraph", "make_checkpointer", "get_graph", "START", "END"]


def make_checkpointer() -> BaseCheckpointSaver:
    """Return the durable Postgres checkpointer, or in-memory if no DB is set.

    The Postgres dependency is imported lazily so the engine runs (and tests
    pass) without ``langgraph-checkpoint-postgres`` installed.
    """

    database_url = get_settings().database_url
    if database_url:
        from langgraph.checkpoint.postgres import PostgresSaver

        checkpointer = PostgresSaver.from_conn_string(database_url)
        checkpointer.setup()
        return checkpointer
    return InMemorySaver()


class CompiledGraph:
    """A durable, runnable graph (systemdesign §6.2).

    ``run`` executes a fresh run keyed by ``run_id``; ``resume`` continues a run
    paused at an ``interrupt()`` by feeding the human's ``Decision``.
    """

    def __init__(self, graph: Any) -> None:
        self._graph = graph

    def _config(self, run_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": run_id}}

    async def run(self, run_id: str, init: GraphState) -> GraphState:
        """Run the graph from ``init``, checkpointing under ``run_id``."""

        result = await self._graph.ainvoke(init, self._config(run_id))
        return GraphState.model_validate(result)

    async def resume(self, run_id: str, decision: Decision) -> GraphState:
        """Resume a HITL-paused run with the human's ``decision``."""

        result = await self._graph.ainvoke(
            Command(resume=decision.model_dump()), self._config(run_id)
        )
        return GraphState.model_validate(result)

    def get_state(self, run_id: str) -> Any:
        """Return the persisted checkpoint snapshot for ``run_id``."""

        return self._graph.get_state(self._config(run_id))


class Harness:
    """Hand-built graph: nodes + static/computed edges (systemdesign §6.2).

    The topology is declared in code here; nodes cannot redirect it. The LLM
    never decides the next step — edges are static or keyed on computed fields.
    """

    def __init__(self) -> None:
        self._builder = StateGraph(GraphState)

    def add_node(self, node: Node) -> None:
        """Register a node under its ``name``."""

        self._builder.add_node(node.name, node)

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
    return harness.compile(checkpointer or InMemorySaver())


@lru_cache(maxsize=1)
def get_graph() -> CompiledGraph:
    """Return the process-wide demo graph with its configured checkpointer."""

    return build_demo_graph(make_checkpointer())
