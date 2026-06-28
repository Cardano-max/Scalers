"""Phase-1 end-to-end slice — the §6.5 seam (HARN-INT).

Composes every Phase-1 piece into one deterministic path:

    load_pack ─▶ graph[ Research(code) ─▶ Assemble(typed Cell) ] ─▶ route(code)
              ─▶ SideEffectBoundary.enqueue ─▶ Dispatcher(mock connector)

* **load_pack** (INFRA-04) loads the per-tenant config.
* the **graph** is the hand-built LangGraph harness (HARN-01) with a durable
  checkpointer; its Assemble node runs a real typed **Cell** (HARN-02) whose
  output is schema-validated or fails on a code path — never raw text downstream.
* **route** (HARN-05) is pure code: auto / review / regenerate from the computed
  confidence, the threshold, the gates, and the channel autonomy.
* on **auto**, the action is enqueued through the exactly-once side-effect
  boundary (HARN-04) and drained by the dispatcher, which fires the (mock)
  connector exactly once even under retry.

The graph run is durable: with ``ENGINE_DATABASE_URL`` set, ``make_checkpointer``
returns the Postgres checkpointer, so a crashed run resumes from its last
checkpoint without re-applying completed nodes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import psycopg
from pydantic_ai.models import KnownModelName, Model

from cells.content_brief import ContentBrief, build_content_brief_cell
from config.loader import load_pack
from config.schema import TenantPack
from langgraph.checkpoint.memory import InMemorySaver

from harness.graph import END, START, Harness
from harness.nodes import ResearchNode
from harness.router import DEFAULT_THRESHOLD, route
from harness.state import AssembleOutput, AutonomyMode, Gate, GraphState, RouteDecision
from sideeffects import Channel, idempotency_key
from sideeffects.boundary import EnqueueStatus, SideEffectBoundary
from sideeffects.dispatcher import Connector, Dispatcher

# Deterministic placeholder confidence for the Phase-1 assemble cell. A real
# self-consistency confidence computer lands in Phase 5; here the slice exercises
# the router with a concrete, stable signal.
ASSEMBLE_CONFIDENCE = 0.9


class AssembleCellNode:
    """Assemble graph node backed by a real typed Cell (HARN-02 boundary).

    Runs eng2's content-brief cell, which returns a schema- and validator-valid
    ``ContentBrief`` or raises ``CellError``. The validated brief is mapped into
    the harness's typed ``AssembleOutput`` so only typed state flows downstream.
    """

    name = "assemble"

    def __init__(self, model: Model | KnownModelName | None = None) -> None:
        self._cell = build_content_brief_cell()
        self._model = model

    async def __call__(self, state: GraphState) -> dict:
        research = state.research
        topic = research.topic if research else state.topic
        findings = research.findings if research else []
        prompt = f"Topic: {topic}\nGrounded findings:\n" + "\n".join(
            f"- {f}" for f in findings
        )
        brief: ContentBrief = await self._cell.run(prompt, model=self._model)
        assembled = AssembleOutput(topic=topic, draft=brief.caption)
        return {
            "assembled": assembled,
            "confidence": ASSEMBLE_CONFIDENCE,
            "step_log": ["assemble"],
        }


def build_slice_graph(assemble_model: Model | KnownModelName | None = None, checkpointer=None):
    """Build the fixed Phase-1 slice graph: START -> research -> assemble -> END.

    ``checkpointer`` defaults to an in-memory saver. Inject the durable Postgres
    checkpointer for crash-resume — once the checkpointer wiring (HARN-03 / dhv.6,
    PR #7) lands its fixed ``make_checkpointer`` becomes the default; the
    integration test constructs an ``AsyncPostgresSaver`` directly meanwhile.
    """
    harness = Harness()
    harness.add_node(ResearchNode())
    harness.add_node(AssembleCellNode(assemble_model))
    harness.add_edge(START, "research")
    harness.add_edge("research", "assemble")
    harness.add_edge("assemble", END)
    return harness.compile(checkpointer or InMemorySaver())


@dataclass
class SliceResult:
    """The outcome of one end-to-end slice run."""

    pack: TenantPack
    state: GraphState
    decision: RouteDecision
    idempotency_key: str | None = None
    enqueue_status: EnqueueStatus | None = None
    dispatched: int = 0
    steps: list[str] = field(default_factory=list)


async def run_slice(
    *,
    tenant_id: str,
    topic: str,
    dsn: str,
    connector: Connector,
    assemble_model: Model | KnownModelName | None = None,
    run_id: str | None = None,
    autonomy: AutonomyMode = AutonomyMode.AUTO,
    threshold: float = DEFAULT_THRESHOLD,
    gates: Sequence[Gate] | None = None,
    channel: Channel = Channel.POSTING,
    target: str = "feed",
    checkpointer=None,
) -> SliceResult:
    """Run the deterministic Phase-1 slice end to end and return what happened.

    Only an ``auto`` decision fires a side effect; ``review`` and ``regenerate``
    short-circuit before the boundary (a human signs off, or the artifact is
    re-generated). Re-running with the same content derives the same idempotency
    key, so a replay never produces a second effect.
    """
    run_id = run_id or f"slice-{tenant_id}-{topic}"
    pack = load_pack(tenant_id)  # INFRA-04: per-tenant config at run start

    graph = build_slice_graph(assemble_model, checkpointer)
    state = await graph.run(
        run_id, GraphState(tenant_id=tenant_id, run_id=run_id, topic=topic)
    )

    decision = route(state.confidence or 0.0, threshold, gates, autonomy)

    result = SliceResult(
        pack=pack, state=state, decision=decision, steps=list(state.step_log)
    )
    if decision is not RouteDecision.AUTO:
        return result

    # Auto: fire the action through the exactly-once boundary.
    draft = state.assembled.draft if state.assembled else ""
    key = idempotency_key(tenant_id, channel, target, draft)
    result.idempotency_key = key

    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=False)
    try:
        async with conn.transaction():
            enq = await SideEffectBoundary().enqueue(
                conn, key, channel, {"draft": draft, "run_id": run_id}
            )
        result.enqueue_status = enq.status
    finally:
        await conn.close()

    result.dispatched = await Dispatcher(dsn, connector).dispatch_pending()
    return result
