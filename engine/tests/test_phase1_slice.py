"""Phase-1 end-to-end integration slice (HARN-INT, systemdesign §6.5).

Proves all four Phase-1 success criteria against a REAL Postgres:

1. ``docker compose up`` substrate (the run + boundary use live Postgres);
2. a deterministic LangGraph run Research -> Assemble(typed cell);
3. an exactly-once mock side effect under forced crash/retry;
4. the pure-code router selecting auto / review / regenerate.

Gated on ``ENGINE_DATABASE_URL`` (matches dhv.5 / PR #7): skipped in the default
no-DB run, executed for real in the integration CI job that provides the DB.
"""

from __future__ import annotations

import os
import uuid

import pytest

from harness.state import AutonomyMode, Gate, GraphState, RouteDecision
from phase1_slice import EnqueueNode, build_slice_graph, run_slice
from sideeffects import Channel
from sideeffects.dispatcher import Dispatcher
from tests.conftest import VALID_BRIEF, tool_model
from tests.mock_connector import MockConnector

# Marked `integration` (dhv.5 / PR #2 convention): excluded from the DB-free unit
# run, executed in CI's pgvector-service job where ENGINE_DATABASE_URL is set.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

TENANT = "ink-studio"  # shipped seed pack (engine/config/packs/ink-studio.toml)


def _model():
    """A deterministic model that drives the content-brief cell to a valid brief."""
    return tool_model(VALID_BRIEF)


# ── Criterion 2: deterministic typed-cell run ────────────────────────────────


async def test_slice_runs_research_then_typed_cell_deterministically(db, dsn):
    result = await run_slice(
        tenant_id=TENANT,
        topic="spring blackwork promo",
        dsn=dsn,
        connector=MockConnector(),
        assemble_model=_model(),
    )
    # load_pack ran.
    assert result.pack.tenant_id == TENANT
    # Research -> Assemble -> (auto) Enqueue executed in order; the typed cell
    # produced the draft, and the enqueue node ran inside the graph.
    assert result.steps == ["research", "assemble", "enqueue"]
    assert result.state.assembled is not None
    assert result.state.assembled.draft == VALID_BRIEF["caption"]
    # Same inputs -> same trajectory (determinism).
    again = await run_slice(
        tenant_id=TENANT, topic="spring blackwork promo", dsn=dsn,
        connector=MockConnector(), assemble_model=_model(),
    )
    assert again.state.assembled.draft == result.state.assembled.draft


# ── Criterion 4: pure-code router auto / review / regenerate ─────────────────


async def test_router_auto_fires_side_effect(db, dsn):
    connector = MockConnector()
    result = await run_slice(
        tenant_id=TENANT, topic="auto", dsn=dsn, connector=connector,
        assemble_model=_model(), autonomy=AutonomyMode.AUTO, threshold=0.85,
    )
    assert result.decision is RouteDecision.AUTO
    assert connector.call_count == 1


async def test_router_review_does_not_fire(db, dsn):
    connector = MockConnector()
    result = await run_slice(
        tenant_id=TENANT, topic="review", dsn=dsn, connector=connector,
        assemble_model=_model(), autonomy=AutonomyMode.REVIEW,
    )
    assert result.decision is RouteDecision.REVIEW
    assert connector.call_count == 0
    assert result.idempotency_key is None


async def test_router_regenerate_on_failed_gate_does_not_fire(db, dsn):
    connector = MockConnector()
    result = await run_slice(
        tenant_id=TENANT, topic="regen", dsn=dsn, connector=connector,
        assemble_model=_model(),
        gates=[Gate(name="banned_phrase", passed=False, detail="off-brand")],
    )
    assert result.decision is RouteDecision.REGENERATE
    assert connector.call_count == 0


# ── Criterion 3a: exactly-once side effect under graph-retry ─────────────────


async def test_replay_same_content_fires_effect_once(db, dsn):
    """Re-running the slice with the same content derives the same idempotency
    key; the second enqueue dedupes, so the connector fires exactly once."""
    connector = MockConnector()

    first = await run_slice(
        tenant_id=TENANT, topic="replayed", dsn=dsn, connector=connector,
        assemble_model=_model(),
    )
    second = await run_slice(
        tenant_id=TENANT, topic="replayed", dsn=dsn, connector=connector,
        assemble_model=_model(),
    )

    assert first.idempotency_key == second.idempotency_key
    assert connector.call_count == 1, "replay must not double-fire"
    rows = await (
        await db.execute(
            "SELECT count(*) FROM outbox WHERE idempotency_key = %s",
            (first.idempotency_key,),
        )
    ).fetchone()
    assert rows[0] == 1


# ── Durability: crash in the state-advance -> enqueue window (CustomerAcq-jmu) ─


async def test_crash_between_state_advance_and_enqueue_does_not_lose_effect(db, dsn):
    """The enqueue lives INSIDE the graph, so a crash after the assemble
    checkpoint but before the enqueue commits leaves the run UNFINISHED (no
    durable 'done' without the outbox intent). On resume the enqueue node runs
    and the effect is present — never lost — and the connector still fires once.

    This is the regression for the at-most-once loss window jmu flagged: with the
    old post-graph enqueue (separate tx) a crash here left a durably-completed run
    with an empty outbox = lost side effect.
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    class CrashOnceEnqueue(EnqueueNode):
        """Crashes on the first attempt BEFORE enqueuing (models a process death
        in the state-advance -> enqueue window), then enqueues normally."""

        def __init__(self, **kw):
            super().__init__(**kw)
            self._crashed = False

        async def __call__(self, state):
            if not self._crashed:
                self._crashed = True
                raise RuntimeError("crash after state-advance, before enqueue commit")
            return await super().__call__(state)

    enqueue_node = CrashOnceEnqueue(
        dsn=dsn, tenant_id=TENANT, channel=Channel.POSTING, target="feed"
    )
    key = enqueue_node.key_for(VALID_BRIEF["caption"])

    async with AsyncPostgresSaver.from_conn_string(dsn) as checkpointer:
        await checkpointer.setup()
        graph = build_slice_graph(
            dsn=dsn,
            tenant_id=TENANT,
            assemble_model=_model(),
            checkpointer=checkpointer,
            enqueue_node=enqueue_node,
        )
        run_id = f"jmu-{uuid.uuid4().hex[:12]}"
        init = GraphState(tenant_id=TENANT, run_id=run_id, topic="durability")
        config = {"configurable": {"thread_id": run_id}}

        # Crash in the enqueue window: the assemble checkpoint is committed, but
        # the outbox intent is NOT yet written.
        with pytest.raises(RuntimeError):
            await graph.run(run_id, init)
        cur = await db.execute(
            "SELECT count(*) FROM outbox WHERE idempotency_key = %s", (key,)
        )
        assert (await cur.fetchone())[0] == 0  # not enqueued yet — but run isn't 'done'

        # Resume: the enqueue node runs and the intent lands. Effect NOT lost.
        await graph._graph.ainvoke(None, config)
        cur = await db.execute(
            "SELECT count(*) FROM outbox WHERE idempotency_key = %s", (key,)
        )
        assert (await cur.fetchone())[0] == 1, (
            "crash in the state-advance -> enqueue window must NOT lose the effect"
        )

    # The recovered intent fires exactly once through the dispatcher.
    connector = MockConnector()
    await Dispatcher(dsn, connector).dispatch_pending()
    assert connector.call_count == 1


# ── Criterion 3b: forced crash resumes exactly once (checkpointer) ───────────


async def test_forced_crash_resumes_without_reapplying_completed_node(dsn):
    """With the durable Postgres checkpointer, a node that crashes mid-run is
    retried on resume but the already-completed node is NOT re-applied — durable,
    exactly-once execution. Uses AsyncPostgresSaver directly (the harness's
    make_checkpointer is finalized by HARN-03 / PR #7)."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from harness.graph import END, START, Harness
    from harness.nodes import ResearchNode

    research_runs: list[str] = []
    assemble_runs: list[str] = []
    crashed = {"done": False}

    class CountingResearch(ResearchNode):
        async def __call__(self, state: GraphState) -> dict:
            research_runs.append(state.run_id)
            return await super().__call__(state)

    class FlakyAssemble:
        name = "assemble"

        async def __call__(self, state: GraphState) -> dict:
            assemble_runs.append(state.run_id)
            if not crashed["done"]:
                crashed["done"] = True
                raise RuntimeError("boom: forced crash mid-run")
            return {"assembled": None, "confidence": 0.9, "step_log": ["assemble"]}

    async with AsyncPostgresSaver.from_conn_string(dsn) as checkpointer:
        await checkpointer.setup()
        harness = Harness()
        harness.add_node(CountingResearch())
        harness.add_node(FlakyAssemble())
        harness.add_edge(START, "research")
        harness.add_edge("research", "assemble")
        harness.add_edge("assemble", END)
        graph = harness.compile(checkpointer)

        # Unique thread per run so the persisted checkpoint is fresh each time.
        run_id = f"crash-{uuid.uuid4().hex[:12]}"
        init = GraphState(tenant_id=TENANT, run_id=run_id, topic="durable")

        config = {"configurable": {"thread_id": run_id}}

        # First attempt crashes inside assemble (research already checkpointed).
        with pytest.raises(RuntimeError):
            await graph.run(run_id, init)

        # Resume from the checkpoint: invoke with None (not the original input),
        # the LangGraph idiom for continuing pending work after a crash. Research
        # is already committed, so only the crashed assemble node retries.
        await graph._graph.ainvoke(None, config)

    assert research_runs.count(run_id) == 1, "completed node re-applied on resume"
    assert assemble_runs.count(run_id) == 2, "only the crashed node retried"
