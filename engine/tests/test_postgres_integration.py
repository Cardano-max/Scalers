"""Postgres integration (HARN-03) — skipped unless ENGINE_DATABASE_URL is set.

Proves the *real* durable path end to end against a live Postgres — the whole
point of HARN-03, which the default in-memory suite cannot cover:

* AC#1: a run completes on the real LangGraph **async** Postgres checkpointer,
  AND a crashed run resumes from the last completed node and finishes once
  (a=1, b=2, c=1) — against real Postgres, not InMemorySaver.
* AC#2: the PostgresRunStore records the JSONB append-only trajectory and the
  query API reads it back.

eng3's docker-compose (INFRA-01) provides the DB; this is skipped in the default
no-DB CI run.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)

# psycopg's async pool cannot use Windows' default ProactorEventLoop. Select a
# SelectorEventLoop policy for the test session on Windows so the async Postgres
# checkpointer can connect. No-op on Linux, where the default loop already works
# and where production runs.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _init(topic: str, run_id: str):
    from harness.state import GraphState

    return GraphState(tenant_id="itest", run_id=run_id, topic=topic)


async def test_run_and_runstore_roundtrip_on_real_postgres():
    from harness.config import get_settings
    from harness.graph import build_demo_graph, make_checkpointer
    from harness.runstore import PostgresRunStore, RunStatus, execute_and_record

    get_settings.cache_clear()  # pick up ENGINE_DATABASE_URL
    checkpointer = await make_checkpointer()  # real AsyncPostgresSaver
    try:
        graph = build_demo_graph(checkpointer)
        store = PostgresRunStore(os.environ["ENGINE_DATABASE_URL"])
        store.setup()

        run_id = f"pg-{uuid.uuid4().hex[:12]}"
        record = await execute_and_record(
            graph,
            store,
            run_id=run_id,
            tenant_id="itest",
            run_type="posting",
            trigger="manual",
            init=_init("postgres", run_id),
        )

        # AC#1 (happy path) on real PG + AC#2 append-only steps recorded.
        assert record.status is RunStatus.COMPLETED
        assert [s.state for s in record.steps] == ["research", "assemble"]
        assert [s.seq for s in record.steps] == [0, 1]
        assert record.auto_count == 1
        # Query API reads it back from Postgres.
        assert store.get_run(run_id).run_id == run_id
        assert any(r.run_id == run_id for r in store.list_runs("itest"))
    finally:
        pool = getattr(checkpointer, "conn", None)
        if pool is not None and hasattr(pool, "close"):
            await pool.close()
        get_settings.cache_clear()


async def test_crash_resume_on_real_postgres_checkpointer():
    from harness.config import get_settings
    from harness.graph import END, START, Harness, make_checkpointer

    get_settings.cache_clear()
    checkpointer = await make_checkpointer()  # real AsyncPostgresSaver
    try:
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
        graph = harness.compile(checkpointer)

        run_id = f"pg-crash-{uuid.uuid4().hex[:12]}"
        with pytest.raises(RuntimeError):
            await graph.run(run_id, _init("x", run_id))

        # Resume from the last completed checkpoint persisted IN POSTGRES.
        final = await graph.recover(run_id)
        assert calls == {"a": 1, "b": 2, "c": 1}  # a not re-applied; b retried; c once
        assert final.step_log == ["a", "b", "c"]
        assert await graph.is_complete(run_id)
    finally:
        pool = getattr(checkpointer, "conn", None)
        if pool is not None and hasattr(pool, "close"):
            await pool.close()
        get_settings.cache_clear()
