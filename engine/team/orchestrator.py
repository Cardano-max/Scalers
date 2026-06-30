"""Team orchestrator (P1) — LangGraph SKELETON for the autonomous marketing team.

Sequences the roles in :data:`team.registry.PIPELINE_ORDER`:

    plan -> research -> strategy -> draft-many-assets -> critique -> queue

The graph is REAL and compiles (LangGraph ``StateGraph``); durable run state uses
the existing run-store pattern (:class:`harness.runstore.PostgresRunStore`) and the
new :class:`team.store.TeamStore`. Sends STAY HELD: the only terminal action is
``queue`` — assets are written with status ``queued`` and a human approves them
later through the existing review path. Nothing here sends.

HONESTY (read this before trusting a run):

* ``plan`` and ``queue`` are REAL: plan emits the role order; queue persists
  whatever assets are actually in state to ``assets`` as ``queued`` (never
  ``sent``). With no assets in state it queues nothing — it does not invent any.
* ``research``, ``strategy``, ``draft_many``, ``critique`` are **skeleton nodes
  with explicit TODOs**. They thread state and log intent but DO NOT yet call the
  underlying cells, because:
    - research/strategist/draft are P0 cells (some on another branch) and the
      draft cell needs per-tenant grounding+platform assembled first
      (see ``team.registry`` notes), and
    - wiring each role's typed output into ``agent_runs`` + ``assets`` and feeding
      the critic an independent pass per asset is the remaining integration work.
  These nodes produce NO fabricated assets. The end-to-end pipeline is therefore
  NOT runnable yet; ``build_team_graph()`` compiles and threads the trajectory.

When the content nodes are wired, each will: build its cell via
``team.registry.build(role, ...)``, run it, persist a row to ``agent_runs``, and
(for producing roles) append a real asset to state for ``queue`` to enqueue. The
critic node will run :func:`cells.critic.build_critic_cell` ONCE per asset as an
independent pass and persist to ``asset_critiques`` — never a staged debate.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from team.registry import PIPELINE_ORDER, Role
from team.store import ASSET_STATUS_QUEUED, TeamStore


# --------------------------------------------------------------------------- #
# Graph state
# --------------------------------------------------------------------------- #


class TeamState(BaseModel):
    """The state threaded through the team graph.

    ``assets`` is the list of produced artifacts (dicts: ``{asset_type, content}``)
    that ``queue`` will enqueue. The skeleton content nodes leave it empty; a wired
    ``draft_many`` populates it. ``step_log`` is the human-readable trajectory.
    """

    campaign_id: str
    run_id: str
    tenant_id: str
    brief: dict[str, Any] = Field(default_factory=dict)
    plan: list[str] = Field(default_factory=list)
    assets: list[dict[str, Any]] = Field(default_factory=list)
    critiques: list[dict[str, Any]] = Field(default_factory=list)
    queued_asset_ids: list[str] = Field(default_factory=list)
    step_log: list[str] = Field(default_factory=list)


# Marker so a reader (and any test) can tell a skeleton step from a real one.
TODO_PREFIX = "TODO(wire):"


# --------------------------------------------------------------------------- #
# Nodes (closures over an optional TeamStore so queue can persist for real)
# --------------------------------------------------------------------------- #


def _plan_node(state: TeamState) -> dict[str, Any]:
    """REAL: emit the role execution order for this campaign."""
    plan = [r.value for r in PIPELINE_ORDER]
    return {
        "plan": plan,
        "step_log": [*state.step_log, f"plan: {' -> '.join(plan)}"],
    }


def _research_node(state: TeamState) -> dict[str, Any]:
    """SKELETON: wire the P0 research pipeline (engine/research) here."""
    return {"step_log": [*state.step_log,
            f"{TODO_PREFIX} research — call research.router/adapter (P0); no output fabricated"]}


def _strategy_node(state: TeamState) -> dict[str, Any]:
    """SKELETON: wire the strategist (content_brief / P0 strategist) here."""
    return {"step_log": [*state.step_log,
            f"{TODO_PREFIX} strategy — registry.build(Role.STRATEGIST) + funnel_architect; no output fabricated"]}


def _draft_many_node(state: TeamState) -> dict[str, Any]:
    """SKELETON: for each planned asset, build copy/draft via the registry and
    append a REAL asset to state. Produces NO fake assets today."""
    return {"step_log": [*state.step_log,
            f"{TODO_PREFIX} draft-many — per funnel asset: registry.build(COPYWRITER/DRAFT) "
            "-> persist agent_runs + append asset; none produced yet"]}


def _critique_node(state: TeamState) -> dict[str, Any]:
    """SKELETON: run the critic ONCE per asset as an INDEPENDENT pass (never a
    staged debate) and persist to asset_critiques. No assets in state -> nothing
    to critique; nothing fabricated."""
    n = len(state.assets)
    return {"step_log": [*state.step_log,
            f"{TODO_PREFIX} critique — independent critic pass over {n} asset(s) "
            "via cells.critic.build_critic_cell; none yet"]}


def _make_queue_node(store: Optional[TeamStore]) -> Callable[[TeamState], dict[str, Any]]:
    """Build the queue node. SENDS STAY HELD: enqueue only, status='queued'.

    Enqueues exactly the assets present in state (none, in the pure skeleton). It
    never sends and never writes a 'sent' status — approval is a separate, human,
    already-gated path.
    """

    def _queue_node(state: TeamState) -> dict[str, Any]:
        queued: list[str] = []
        for asset in state.assets:
            asset_id = asset.get("id") or uuid.uuid4().hex
            if store is not None:
                store.record_asset(
                    id=asset_id,
                    campaign_id=state.campaign_id,
                    asset_type=asset.get("asset_type", "unknown"),
                    content=asset.get("content", {}),
                    status=ASSET_STATUS_QUEUED,  # held for human approval; never 'sent'
                )
            queued.append(asset_id)
        note = (f"queue: enqueued {len(queued)} asset(s) as '{ASSET_STATUS_QUEUED}' "
                "(sends HELD — approve-first)")
        return {"queued_asset_ids": queued, "step_log": [*state.step_log, note]}

    return _queue_node


# --------------------------------------------------------------------------- #
# Graph construction
# --------------------------------------------------------------------------- #


def build_team_graph(*, store: Optional[TeamStore] = None, checkpointer: Any | None = None):
    """Build and compile the team graph (plan -> ... -> queue).

    Real and compilable. The content nodes are skeleton TODOs (see module
    docstring); ``store`` lets the queue node persist real assets when present.

    ``checkpointer`` defaults to an in-memory saver. INTEGRATION TODO: pass the
    LangGraph Postgres checkpointer (langgraph-checkpoint-postgres, already a dep —
    see harness.graph for the AsyncPostgresSaver wiring) for durable replay.
    """
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(TeamState)
    builder.add_node("plan", _plan_node)
    builder.add_node("research", _research_node)
    builder.add_node("strategy", _strategy_node)
    builder.add_node("draft_many", _draft_many_node)
    builder.add_node("critique", _critique_node)
    builder.add_node("queue", _make_queue_node(store))

    builder.add_edge(START, "plan")
    builder.add_edge("plan", "research")
    builder.add_edge("research", "strategy")
    builder.add_edge("strategy", "draft_many")
    builder.add_edge("draft_many", "critique")
    builder.add_edge("critique", "queue")
    builder.add_edge("queue", END)

    return builder.compile(checkpointer=checkpointer or InMemorySaver())


# --------------------------------------------------------------------------- #
# Orchestrator facade
# --------------------------------------------------------------------------- #


class TeamOrchestrator:
    """Facade that owns the durable stores and the compiled team graph.

    ``__init__`` is REAL: it sets up the durable schema (run store + team store,
    both idempotent ``CREATE TABLE IF NOT EXISTS``). Running the graph end-to-end
    is SKELETON — see ``run_skeleton`` — because the content nodes are not wired.
    """

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.environ.get("ENGINE_DATABASE_URL") \
            or "postgresql://scalers:scalers@localhost:5432/scalers"
        self.team_store = TeamStore(self.dsn)
        # Run-store is imported lazily so the in-memory/test path needs no driver.
        from harness.runstore import PostgresRunStore

        self.run_store = PostgresRunStore(self.dsn)

    def setup(self) -> None:
        """Apply both durable schemas (idempotent)."""
        self.run_store.setup()
        self.team_store.setup()

    def plan(self) -> list[str]:
        """REAL: the role execution order without running anything."""
        return [r.value for r in PIPELINE_ORDER]

    def run_skeleton(
        self,
        *,
        campaign_id: str,
        tenant_id: str,
        brief: dict[str, Any] | None = None,
        persist: bool = False,
    ) -> TeamState:
        """Run the compiled graph as a SKELETON and return the final state.

        This threads the trajectory and (if assets existed) would enqueue them
        HELD. It does NOT produce real assets — the content nodes are TODOs — so a
        run today returns a populated ``step_log`` and an EMPTY ``assets`` list.
        Set ``persist=True`` to give the queue node the team store (only matters
        once draft_many populates real assets).

        TODO: replace this skeleton driver with the wired pipeline (real cells +
        agent_runs/assets/asset_critiques persistence) once the P0 content cells
        and per-tenant grounding assembly are integrated.
        """
        graph = build_team_graph(store=self.team_store if persist else None)
        run_id = f"team-{campaign_id}-{uuid.uuid4().hex[:12]}"
        init = TeamState(
            campaign_id=campaign_id, run_id=run_id, tenant_id=tenant_id,
            brief=brief or {},
        )
        config = {"configurable": {"thread_id": run_id}}
        final = graph.invoke(init, config=config)
        return TeamState.model_validate(final)
