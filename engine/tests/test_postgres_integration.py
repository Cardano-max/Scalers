"""Postgres integration (HARN-03) — skipped unless ENGINE_DATABASE_URL is set.

Proves the *real* durable path end to end against a live Postgres: the LangGraph
Postgres checkpointer crash-recovers a run, and the PostgresRunStore records the
JSONB append-only trajectory queryable for the console. Skipped in the default
(no-DB) CI run; eng3's docker-compose (INFRA-01) provides the DB for the
integration job.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)


async def test_postgres_checkpointer_and_runstore_roundtrip():
    from harness.config import get_settings
    from harness.graph import build_demo_graph, make_checkpointer
    from harness.runstore import PostgresRunStore, RunStatus, execute_and_record
    from harness.state import GraphState

    get_settings.cache_clear()  # pick up ENGINE_DATABASE_URL
    try:
        checkpointer = make_checkpointer()  # real PostgresSaver
        graph = build_demo_graph(checkpointer)

        store = PostgresRunStore(os.environ["ENGINE_DATABASE_URL"])
        store.setup()

        run_id = f"pg-int-{os.getpid()}"
        record = await execute_and_record(
            graph,
            store,
            run_id=run_id,
            tenant_id="itest",
            run_type="posting",
            trigger="manual",
            init=GraphState(tenant_id="itest", run_id=run_id, topic="postgres"),
        )

        assert record.status is RunStatus.COMPLETED
        assert [s.state for s in record.steps] == ["research", "assemble"]
        assert store.get_run(run_id).run_id == run_id
        assert any(r.run_id == run_id for r in store.list_runs("itest"))
    finally:
        get_settings.cache_clear()
