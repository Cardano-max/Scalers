"""Phase-1 end-to-end slice — the §6.5 seam (HARN-INT).

Composes every Phase-1 piece into one deterministic path:

    load_pack ─▶ graph[ Research(code) ─▶ Assemble(typed Cell) ─▶ route ─▶ Enqueue ]
              ─▶ Dispatcher(mock connector)

* **load_pack** (INFRA-04) loads the per-tenant config.
* the **graph** is the hand-built LangGraph harness (HARN-01) with a durable
  checkpointer; its Assemble node runs a real typed **Cell** (HARN-02) whose
  output is schema-validated or fails on a code path — never raw text downstream.
* **route** (HARN-05) is pure code wired as a CONDITIONAL EDGE: auto / review /
  regenerate from the computed confidence, the threshold, the gates, and the
  channel autonomy. Only ``auto`` flows to the Enqueue node.
* the **Enqueue node** writes the side-effect intent through the exactly-once
  boundary (HARN-04). It lives INSIDE the graph on purpose: the graph is never
  durably "done" until the enqueue node has run, so the checkpointer's
  at-least-once node execution + the idempotent ``ON CONFLICT`` enqueue couple
  the outbox intent to the durable state advance — a crash after the state
  advance but before the enqueue cannot lose the effect (it resumes and enqueues;
  a redundant resume dedupes). This realizes boundary.py's "outbox written with
  the state advance" property end to end.
* the **Dispatcher** then drains the outbox, firing the (mock) connector exactly
  once even under retry.
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


def _confidence_of(state) -> float:
    """Read ``confidence`` whether the graph hands us a model or a mapping."""
    if isinstance(state, dict):
        return state.get("confidence") or 0.0
    return getattr(state, "confidence", None) or 0.0


def _draft_of(state) -> str:
    assembled = state["assembled"] if isinstance(state, dict) else state.assembled
    return assembled.draft if assembled else ""


def _run_id_of(state) -> str:
    return state["run_id"] if isinstance(state, dict) else state.run_id


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


class EnqueueNode:
    """Graph node that writes the side-effect intent to the outbox (HARN-04).

    Deliberately a graph node, NOT a post-graph step: the checkpointer only
    records the run as advanced past here AFTER this node has committed its
    enqueue, so a crash in the state-advance→enqueue window leaves the run
    *unfinished* (it resumes and enqueues) rather than *finished-without-intent*
    (the lost-effect bug). The enqueue is idempotent (``ON CONFLICT``), so a
    resume that re-runs this node never double-enqueues. Derives the key from the
    durable draft, so the same content always maps to the same outbox row.
    """

    name = "enqueue"

    def __init__(self, *, dsn: str, tenant_id: str, channel: Channel, target: str) -> None:
        self._dsn = dsn
        self._tenant_id = tenant_id
        self._channel = channel
        self._target = target

    def key_for(self, draft: str) -> str:
        return idempotency_key(self._tenant_id, self._channel, self._target, draft)

    async def __call__(self, state) -> dict:
        draft = _draft_of(state)
        key = self.key_for(draft)
        conn = await psycopg.AsyncConnection.connect(self._dsn, autocommit=False)
        try:
            async with conn.transaction():
                await SideEffectBoundary().enqueue(
                    conn, key, self._channel, {"draft": draft, "run_id": _run_id_of(state)}
                )
        finally:
            await conn.close()
        return {"step_log": ["enqueue"]}


def _make_route_edge(threshold: float, gates: Sequence[Gate] | None, autonomy: AutonomyMode):
    """Conditional-edge function: only an ``auto`` decision flows to enqueue."""

    def choose(state) -> str:
        decision = route(_confidence_of(state), threshold, gates, autonomy)
        return "enqueue" if decision is RouteDecision.AUTO else END

    return choose


def build_slice_graph(
    *,
    dsn: str,
    tenant_id: str,
    assemble_model: Model | KnownModelName | None = None,
    autonomy: AutonomyMode = AutonomyMode.AUTO,
    threshold: float = DEFAULT_THRESHOLD,
    gates: Sequence[Gate] | None = None,
    channel: Channel = Channel.POSTING,
    target: str = "feed",
    checkpointer=None,
    enqueue_node: EnqueueNode | None = None,
):
    """Build the Phase-1 slice graph: research -> assemble -> route -> [enqueue|END].

    ``checkpointer`` defaults to an in-memory saver; inject the durable Postgres
    checkpointer for crash-resume. ``enqueue_node`` can be overridden (e.g. a
    crash-injecting subclass in tests).
    """
    harness = Harness()
    harness.add_node(ResearchNode())
    harness.add_node(AssembleCellNode(assemble_model))
    harness.add_node(
        enqueue_node
        or EnqueueNode(dsn=dsn, tenant_id=tenant_id, channel=channel, target=target)
    )
    harness.add_edge(START, "research")
    harness.add_edge("research", "assemble")
    harness.add_conditional("assemble", _make_route_edge(threshold, gates, autonomy))
    harness.add_edge("enqueue", END)
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

    The enqueue happens INSIDE the graph (durably coupled to the state advance);
    only an ``auto`` decision reaches it. Re-running with the same content derives
    the same idempotency key, so a replay never produces a second effect.
    """
    run_id = run_id or f"slice-{tenant_id}-{topic}"
    pack = load_pack(tenant_id)  # INFRA-04: per-tenant config at run start

    graph = build_slice_graph(
        dsn=dsn,
        tenant_id=tenant_id,
        assemble_model=assemble_model,
        autonomy=autonomy,
        threshold=threshold,
        gates=gates,
        channel=channel,
        target=target,
        checkpointer=checkpointer,
    )
    state = await graph.run(
        run_id, GraphState(tenant_id=tenant_id, run_id=run_id, topic=topic)
    )

    decision = route(state.confidence or 0.0, threshold, gates, autonomy)
    result = SliceResult(
        pack=pack, state=state, decision=decision, steps=list(state.step_log)
    )
    if decision is not RouteDecision.AUTO:
        return result

    # The intent was enqueued by the in-graph Enqueue node; drain it now.
    draft = state.assembled.draft if state.assembled else ""
    result.idempotency_key = idempotency_key(tenant_id, channel, target, draft)
    result.enqueue_status = EnqueueStatus.ENQUEUED
    result.dispatched = await Dispatcher(dsn, connector).dispatch_pending()
    return result
