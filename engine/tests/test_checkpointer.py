"""Durable checkpointer tests (HARN-03): crash recovery + replay guard (fk5).

Asserts a crashed run resumes from the last completed node and finishes exactly
once (no node re-applied), and that replaying a COMPLETED ``run_id`` is rejected
rather than re-accumulating append-reduced channels (CustomerAcq-fk5).
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from harness.graph import (
    END,
    START,
    Harness,
    RunAlreadyCompletedError,
    RunInProgressError,
    build_demo_graph,
)
from harness.serde import make_serde
from harness.state import GraphState


def _init(topic: str, run_id: str) -> GraphState:
    return GraphState(tenant_id="t", run_id=run_id, topic=topic)


async def test_crash_midrun_resumes_from_last_node_exactly_once():
    calls = {"a": 0, "b": 0, "c": 0}
    fail_once = {"b": True}

    class A:
        name = "a"

        async def __call__(self, state):
            calls["a"] += 1
            return {"step_log": ["a"]}

    class B:
        name = "b"

        async def __call__(self, state):
            calls["b"] += 1
            if fail_once["b"]:
                fail_once["b"] = False
                raise RuntimeError("crash in b")
            return {"step_log": ["b"]}

    class C:
        name = "c"

        async def __call__(self, state):
            calls["c"] += 1
            return {"step_log": ["c"]}

    harness = Harness()
    harness.add_node(A())
    harness.add_node(B())
    harness.add_node(C())
    harness.add_edge(START, "a")
    harness.add_edge("a", "b")
    harness.add_edge("b", "c")
    harness.add_edge("c", END)
    graph = harness.compile(InMemorySaver(serde=make_serde()))

    # Run crashes inside node b.
    with pytest.raises(RuntimeError):
        await graph.run("crash", _init("x", "crash"))

    # run() refuses to restart an in-progress (crashed) thread.
    with pytest.raises(RunInProgressError):
        await graph.run("crash", _init("x", "crash"))

    # recover() resumes from the last checkpoint.
    final = await graph.recover("crash")

    # a ran once (not re-applied), b crashed then retried, c ran once.
    assert calls == {"a": 1, "b": 2, "c": 1}
    assert final.step_log == ["a", "b", "c"]
    assert await graph.is_complete("crash")


async def test_replay_of_completed_thread_is_rejected_fk5():
    graph = build_demo_graph()
    first = await graph.run("dup", _init("x", "dup"))
    assert first.step_log == ["research", "assemble"]

    # fk5: re-running a completed thread_id would replay the checkpoint and make
    # append-reduced channels accumulate. The guard rejects it instead.
    with pytest.raises(RunAlreadyCompletedError):
        await graph.run("dup", _init("x", "dup"))

    # State is unchanged — no accumulation leaked into step_log.
    assert (await graph.get_state("dup")).values["step_log"] == ["research", "assemble"]


async def test_fresh_thread_runs_normally():
    graph = build_demo_graph()
    final = await graph.run("fresh", _init("y", "fresh"))
    assert final.step_log == ["research", "assemble"]
    assert await graph.is_complete("fresh")


async def test_gates_jury_are_last_value_not_accumulating():
    # Defense-in-depth for fk5: router-input channels overwrite, never append.
    from harness.state import Gate

    class Setter:
        name = "setter"

        def __init__(self, gate_name: str):
            self._gate = gate_name

        async def __call__(self, state):
            return {"gates": [Gate(name=self._gate, passed=True)], "step_log": [self._gate]}

    harness = Harness()
    harness.add_node(Setter("first"))
    second = Setter("second")
    second.name = "second"
    harness.add_node(second)
    harness.add_edge(START, "setter")
    harness.add_edge("setter", "second")
    harness.add_edge("second", END)
    graph = harness.compile(InMemorySaver(serde=make_serde()))

    final = await graph.run("g", _init("x", "g"))
    # Two nodes each wrote one gate; last-value means only the latest survives.
    assert [g.name for g in final.gates] == ["second"]
