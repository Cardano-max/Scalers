"""Structured span emission tests (OBS-01).

Covers node spans with duration + I/O, nested cell/gate/tool spans + parent
linkage, failed spans, truncation, the redaction seam, and best-effort Langfuse
mirroring (PG persistence must succeed even if Langfuse is down).
"""

from __future__ import annotations

import pytest

from harness.graph import END, START, Harness, build_demo_graph
from harness.runstore import InMemoryRunStore, RunStatus, execute_and_record
from harness.spans import MAX_IO_CHARS, set_redactor, span
from harness.state import GraphState


def _init(topic: str, run_id: str) -> GraphState:
    return GraphState(tenant_id="t", run_id=run_id, topic=topic)


async def _run(graph, store, run_id="r1", topic="launch", **kw):
    return await execute_and_record(
        graph, store, run_id=run_id, tenant_id="t", run_type="posting",
        trigger="manual", init=_init(topic, run_id), **kw,
    )


async def test_node_spans_have_duration_io_status_and_order():
    record = await _run(build_demo_graph(), InMemoryRunStore())
    assert [s.node for s in record.steps] == ["research", "assemble"]
    for s in record.steps:
        assert s.kind == "node"
        assert s.status == "ok"
        assert s.duration_ms is not None and s.duration_ms >= 0
        assert s.start_ts and s.end_ts
        assert s.input is not None and s.output is not None
        assert s.span_id and s.run_id == "r1"


async def test_cell_span_nested_under_node():
    record = await _run(build_demo_graph(), InMemoryRunStore())
    research = record.steps[0]
    assert [c.kind for c in research.children] == ["cell"]
    cell = research.children[0]
    assert cell.node == "cell:ResearchOutput"
    assert cell.parent_span_id == research.span_id  # parent linkage


async def test_back_compat_legacy_step_fields_preserved():
    record = await _run(build_demo_graph(), InMemoryRunStore())
    # Old Runs/Overview readers use .state / .seq / .at — still populated.
    assert [s.state for s in record.steps] == ["research", "assemble"]
    assert [s.seq for s in record.steps] == [0, 1]
    assert all(s.at for s in record.steps)


async def test_failed_span_marks_status_and_is_identifiable():
    class Boom:
        name = "boom"

        async def __call__(self, state):
            raise RuntimeError("kaboom")

    h = Harness()
    h.add_node(Boom())
    h.add_edge(START, "boom")
    h.add_edge("boom", END)
    store = InMemoryRunStore()
    with pytest.raises(RuntimeError):
        await execute_and_record(
            h.compile(__import__("langgraph.checkpoint.memory", fromlist=["InMemorySaver"]).InMemorySaver()),
            store, run_id="f1", tenant_id="t", run_type="posting",
            trigger="manual", init=_init("x", "f1"),
        )
    record = store.get_run("f1")
    assert record.status is RunStatus.FAILED
    failed = record.failed_step
    assert failed is not None and failed.node == "boom"
    assert failed.status == "failed" and "kaboom" in failed.error
    assert record.last_step.node == "boom"


async def test_gate_and_tool_spans_via_helper_with_parent_linkage():
    class Worker:
        name = "worker"

        async def __call__(self, state):
            with span("voice-gate", kind="gate"):
                pass
            with span("meta-publish", kind="tool"):
                pass
            return {"step_log": ["worker"]}

    h = Harness()
    h.add_node(Worker())
    h.add_edge(START, "worker")
    h.add_edge("worker", END)
    from langgraph.checkpoint.memory import InMemorySaver

    store = InMemoryRunStore()
    await execute_and_record(
        h.compile(InMemorySaver()), store, run_id="g1", tenant_id="t",
        run_type="posting", trigger="manual", init=_init("x", "g1"),
    )
    node = store.get_run("g1").steps[0]
    kinds = [(c.node, c.kind) for c in node.children]
    assert ("voice-gate", "gate") in kinds
    assert ("meta-publish", "tool") in kinds
    assert all(c.parent_span_id == node.span_id for c in node.children)


async def test_large_io_is_truncated():
    big = "x" * (MAX_IO_CHARS + 500)

    class Big:
        name = "big"

        async def __call__(self, state):
            return {"step_log": [big]}

    from langgraph.checkpoint.memory import InMemorySaver

    h = Harness()
    h.add_node(Big())
    h.add_edge(START, "big")
    h.add_edge("big", END)
    store = InMemoryRunStore()
    await execute_and_record(
        h.compile(InMemorySaver()), store, run_id="b1", tenant_id="t",
        run_type="posting", trigger="manual", init=_init("x", "b1"),
    )
    out = store.get_run("b1").steps[0]
    assert out.output_truncated
    assert len(out.output) <= MAX_IO_CHARS + 40  # truncation marker headroom


async def test_redaction_seam_applies_to_io():
    def redactor(value):
        return "[REDACTED]"

    set_redactor(redactor)
    try:
        record = await _run(build_demo_graph(), InMemoryRunStore(), run_id="red1")
    finally:
        set_redactor(None)
    assert record.steps[0].input == "[REDACTED]"


# --- best-effort Langfuse mirroring (PG persists even if Langfuse is down) ---


async def test_run_completes_even_if_langfuse_raises(monkeypatch):
    class Raising:
        def trace(self, *a, **k):
            raise RuntimeError("langfuse down")

    import observability

    monkeypatch.setattr(observability, "get_langfuse", lambda: Raising())
    record = await _run(build_demo_graph(), InMemoryRunStore(), run_id="lf1")
    # Run still succeeds and spans are persisted despite the mirror failing.
    assert record.status is RunStatus.COMPLETED
    assert [s.node for s in record.steps] == ["research", "assemble"]


def test_mirror_run_returns_false_when_unconfigured(monkeypatch):
    import observability

    monkeypatch.setattr(observability, "get_langfuse", lambda: None)
    assert observability.mirror_run("r", "t", []) is False


def test_mirror_run_sends_to_fake_client():
    import observability
    from harness.spans import Span

    recorded = []

    class FakeTrace:
        def span(self, **kw):
            recorded.append(kw["name"])
            return self

    class FakeClient:
        def trace(self, **kw):
            return FakeTrace()

        def flush(self):
            pass

    parent = Span(span_id="1", run_id="r", node="research", start_ts="t")
    parent.children.append(Span(span_id="2", run_id="r", node="cell:X", kind="cell", start_ts="t"))
    assert observability.mirror_run("r", "t", [parent], client=FakeClient()) is True
    assert recorded == ["research", "cell:X"]
