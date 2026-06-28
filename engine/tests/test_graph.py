"""Graph tests (HARN-01): deterministic Research -> Assemble + run/resume HITL.

Exercises the systemdesign §6.2 control-core interfaces: ``Harness``,
``CompiledGraph.run`` / ``resume``, and the fixed topology.
"""

from __future__ import annotations

import asyncio

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt

from harness.graph import END, START, Harness, build_demo_graph
from harness.nodes import CellError, typed_cell
from harness.state import (
    AssembleOutput,
    Decision,
    GraphState,
    ResearchOutput,
)


def _init(topic: str, run_id: str) -> GraphState:
    return GraphState(tenant_id="t", run_id=run_id, topic=topic)


def test_research_then_assemble_runs_end_to_end():
    graph = build_demo_graph()
    final = asyncio.run(graph.run("r1", _init("cold email", "r1")))

    assert isinstance(final.research, ResearchOutput)
    assert isinstance(final.assembled, AssembleOutput)
    assert final.assembled.topic == "cold email"
    assert "cold email" in final.assembled.draft
    assert 0.0 <= final.confidence <= 1.0
    assert final.step_log == ["research", "assemble"]


def test_graph_is_deterministic():
    graph = build_demo_graph()
    a = asyncio.run(graph.run("a", _init("growth", "a")))
    b = asyncio.run(graph.run("b", _init("growth", "b")))
    assert a.assembled.draft == b.assembled.draft
    assert a.confidence == b.confidence


def test_node_order_is_fixed():
    # Research output feeds Assemble — proving the fixed edge order in code.
    graph = build_demo_graph()
    final = asyncio.run(graph.run("order", _init("x", "order")))
    assert final.research.findings  # research ran first
    for finding in final.research.findings:
        assert finding in final.assembled.draft


def test_checkpointer_persists_run_state():
    graph = build_demo_graph()
    asyncio.run(graph.run("resume-me", _init("resumable", "resume-me")))
    snapshot = asyncio.run(graph.get_state("resume-me"))
    assert snapshot.values["assembled"].topic == "resumable"
    assert snapshot.values["confidence"] > 0


# --- run / resume (HITL interrupt) — systemdesign §6.2 + §7 ---


class _ApprovalGate:
    """A node that pauses for human approval via LangGraph ``interrupt()``."""

    name = "approval"

    async def __call__(self, state: GraphState):
        decision = interrupt({"ask": "approve?"})
        return {"decision": decision["action"], "step_log": ["approval"]}


def _build_hitl_graph():
    harness = Harness()
    harness.add_node(_ApprovalGate())
    harness.add_edge(START, "approval")
    harness.add_edge("approval", END)
    return harness.compile(InMemorySaver())


def test_run_pauses_at_interrupt_then_resume_continues():
    graph = _build_hitl_graph()
    # run() pauses at interrupt; decision is not yet set.
    paused = asyncio.run(graph.run("hitl", _init("x", "hitl")))
    assert paused.decision is None

    # resume() continues the paused run with the human's approval.
    resumed = asyncio.run(graph.resume("hitl", Decision(action="approve")))
    assert resumed.decision == "approve"
    assert resumed.step_log == ["approval"]


# --- typed-cell boundary (HARN-01/02 seam) ---


def test_typed_cell_rejects_invalid_payload():
    with pytest.raises(CellError):
        typed_cell(ResearchOutput, {"findings": ["missing topic"]})


def test_typed_cell_accepts_valid_payload():
    out = typed_cell(ResearchOutput, {"topic": "ok", "findings": ["a"]})
    assert out.topic == "ok"
