"""Durable run-state store tests (HARN-03 / systemdesign §5.1).

Append-only ``steps[]``, unique run_id, status/counters, the query API
(``get_run`` trajectory / ``list_runs`` history), and the ``execute_and_record``
run-driver that feeds the console Runs/Overview.
"""

from __future__ import annotations

import pytest

from harness.graph import build_demo_graph
from harness.runstore import (
    InMemoryRunStore,
    RunExistsError,
    RunStatus,
    execute_and_record,
)
from harness.state import AutonomyMode, GraphState


def _init(topic: str, run_id: str) -> GraphState:
    return GraphState(tenant_id="t", run_id=run_id, topic=topic)


def test_steps_are_append_only_and_sequenced():
    store = InMemoryRunStore()
    store.start_run("r1", "t", "posting", "manual")
    store.append_step("r1", text="research completed", state="research")
    store.append_step("r1", text="assemble completed", state="assemble")

    run = store.get_run("r1")
    assert [s.state for s in run.steps] == ["research", "assemble"]
    assert [s.seq for s in run.steps] == [0, 1]
    assert run.status is RunStatus.RUNNING


def test_unique_run_id_enforced():
    store = InMemoryRunStore()
    store.start_run("dup", "t", "posting", "manual")
    with pytest.raises(RunExistsError):
        store.start_run("dup", "t", "posting", "manual")


def test_finish_run_sets_status_and_counters():
    store = InMemoryRunStore()
    store.start_run("r1", "t", "posting", "manual")
    store.finish_run("r1", status=RunStatus.COMPLETED, auto_count=1)
    run = store.get_run("r1")
    assert run.status is RunStatus.COMPLETED
    assert run.auto_count == 1
    assert run.review_count == 0


def test_list_runs_scopes_by_tenant_history():
    store = InMemoryRunStore()
    store.start_run("r1", "tA", "posting", "manual")
    store.start_run("r2", "tA", "posting", "webhook")
    store.start_run("r3", "tB", "outreach", "manual")
    assert {r.run_id for r in store.list_runs("tA")} == {"r1", "r2"}
    assert [r.run_id for r in store.list_runs("tB")] == ["r3"]


def test_get_run_missing_returns_none():
    assert InMemoryRunStore().get_run("nope") is None


async def test_execute_and_record_drives_graph_and_records_trajectory():
    graph = build_demo_graph()
    store = InMemoryRunStore()
    record = await execute_and_record(
        graph,
        store,
        run_id="e1",
        tenant_id="t",
        run_type="posting",
        trigger="manual",
        init=_init("launch", "e1"),
    )
    assert record.status is RunStatus.COMPLETED
    assert [s.state for s in record.steps] == ["research", "assemble"]
    assert record.auto_count == 1  # confidence 0.9 -> auto
    assert record.review_count == 0
    # Persisted and queryable for the console read models.
    assert store.get_run("e1").run_id == "e1"
    assert store.list_runs("t")[0].run_id == "e1"


async def test_execute_and_record_review_autonomy_counts_review():
    graph = build_demo_graph()
    store = InMemoryRunStore()
    record = await execute_and_record(
        graph,
        store,
        run_id="e2",
        tenant_id="t",
        run_type="posting",
        trigger="manual",
        init=_init("x", "e2"),
        autonomy=AutonomyMode.REVIEW,
    )
    assert record.review_count == 1
    assert record.auto_count == 0


async def test_execute_and_record_rejects_duplicate_run_id():
    graph = build_demo_graph()
    store = InMemoryRunStore()
    kwargs = dict(
        run_id="dup",
        tenant_id="t",
        run_type="posting",
        trigger="manual",
        init=_init("x", "dup"),
    )
    await execute_and_record(graph, store, **kwargs)
    # Re-driving the same run_id is rejected at the store (fk5 durable guard).
    with pytest.raises(RunExistsError):
        await execute_and_record(graph, store, **kwargs)
