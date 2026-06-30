"""Compose + run a campaign through the WIRED spine for a selected archetype (§3, §6 A).

This is the realization of the team spine's previously-stubbed nodes
(``team.orchestrator``'s research/strategy/draft_many/critique ``TODO(wire)``): the
nodes here call the REAL cells, the fan-out is a capped LangGraph ``Send``, the
verdict is the pure-code ``harness.router.route`` (never a model emission), and the
ONLY terminal effect is a HELD/PENDING row — nothing sends.

Topology is FIXED and compiled once; the :class:`ArchetypeSpec` only TOGGLES
pre-declared nodes (via :func:`archetypes.router.route_archetype`) and parameterizes
them (channels, fanout_cap). The model fills CONTENT (strategy, draft, critique) and
emits a bounded label (classify) — it never edits graph shape.

Honesty:
  * Real cell model calls only — ``build_strategy_cell``, ``build_content_brief_cell``,
    ``build_critic_cell``. No canned content.
  * ``research`` runs the real research pipeline; with no provider key it degrades to
    honest-empty (KB-only) and records that — it never fabricates citations.
  * ``draft_many`` uses the content-brief cell as the per-channel draft producer.
    HONEST TODO: the canonical ``cells.draft.build_draft_cell`` needs a per-tenant
    ``VoiceGrounding`` + ``Platform`` assembled first (see ``team.registry`` Role.DRAFT
    note); that assembly is out of Phase-A scope, so content_brief (a real, validated
    cell) stands in. Not faked — a different real cell.
  * Every produced asset becomes a PENDING ``actions`` row (approve-first). Nothing
    flips to sent here.
"""

from __future__ import annotations

import operator
import os
import uuid
from typing import Annotated, Any

from pydantic import BaseModel, Field

from archetypes import registry, router
from archetypes.spec import ArchetypeSpec, StepKind


# --------------------------------------------------------------------------- #
# State (fan-in safe: assets carry an additive reducer for the Send workers).
# --------------------------------------------------------------------------- #


class CampaignState(BaseModel):
    campaign_id: str
    run_id: str
    tenant_id: str
    archetype_id: str
    brief: str = ""
    strategy_text: str = ""
    research_text: str = ""
    # Per-worker channel, set on the Send payload for a draft_one worker. Workers
    # never write it back, so there is no concurrent-write conflict at fan-in.
    channel: str = ""
    # additive reducer so parallel draft_one workers accumulate without clobbering.
    assets: Annotated[list[dict[str, Any]], operator.add] = Field(default_factory=list)
    critiques: Annotated[list[dict[str, Any]], operator.add] = Field(default_factory=list)
    queued_asset_ids: Annotated[list[str], operator.add] = Field(default_factory=list)
    pending_action_ids: Annotated[list[str], operator.add] = Field(default_factory=list)
    step_log: Annotated[list[str], operator.add] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Node implementations (closures over the durable stores + dsn).
# --------------------------------------------------------------------------- #


def _record_run(team_store, *, campaign_id, run_id, role, model, inp, out) -> None:
    if team_store is None:
        return
    team_store.record_agent_run(
        id=f"ar_{uuid.uuid4().hex[:16]}", campaign_id=campaign_id, run_id=run_id,
        role=role, model=model, input=inp, output=out,
    )


def _plan_node(state: CampaignState) -> dict[str, Any]:
    spec = registry.get(state.archetype_id)
    path = router.enabled_path(spec)
    return {"step_log": [f"plan[{spec.id}]: {' -> '.join(path)}"]}


def _make_research_node(team_store, dsn):
    def _research_node(state: CampaignState) -> dict[str, Any]:
        """REAL research pipeline; honest-empty (no fabrication) when no provider key."""
        from cells.base import DEFAULT_MODEL
        try:
            from research.adapter import Intent, ResearchQuery
            from research.pipeline import gather_and_persist, live_registry
            from research.router import ResearchRouter

            reg = live_registry()  # firecrawl/exa enabled only when key present
            r = ResearchRouter(list(reg.values()))
            query = ResearchQuery(
                intent=Intent.DEMAND, niche="tattoo studio", tenant_id=state.tenant_id,
            )
            result, ids = gather_and_persist(
                r, query, run_id=state.run_id, tenant_id=state.tenant_id, dsn=dsn,
            )
            cited = list(getattr(result, "sources_cited", []) or [])
            text = "\n".join(
                f"- {s.get('title') or s.get('url')}" for s in (dict(x) for x in cited)
            )
            note = (f"research: {len(cited)} cited source(s), {len(ids)} persisted"
                    if cited else
                    "research: no live provider key -> honest-empty (KB-only); 0 citations")
            _record_run(team_store, campaign_id=state.campaign_id, run_id=state.run_id,
                        role="researcher", model=DEFAULT_MODEL,
                        inp={"intent": "demand", "tenant": state.tenant_id},
                        out={"cited": len(cited), "persisted": len(ids)})
            return {"research_text": text, "step_log": [note]}
        except Exception as exc:  # never fabricate; record the honest degrade
            return {"step_log": [f"research: degraded ({type(exc).__name__}); 0 citations, no fabrication"]}

    return _research_node


def _make_strategy_node(team_store):
    def _strategy_node(state: CampaignState) -> dict[str, Any]:
        """REAL strategy cell (Sonnet, temp 0) -> CampaignStrategy."""
        from cells.strategy import build_strategy_cell, build_strategy_prompt, render_strategy

        cell = build_strategy_cell()
        prompt = build_strategy_prompt(
            state.tenant_id, state.brief or f"{state.archetype_id} campaign",
            research=state.research_text or None,
        )
        strategy = cell.run_sync(prompt)
        text = render_strategy(strategy)
        _record_run(team_store, campaign_id=state.campaign_id, run_id=state.run_id,
                    role="strategist", model=cell.model if isinstance(cell.model, str) else str(cell.model),
                    inp={"brief": state.brief}, out=strategy.model_dump())
        return {"strategy_text": text, "step_log": [f"strategy: angle set ({len(text)} chars)"]}

    return _strategy_node


def _draft_dispatch_node(state: CampaignState) -> dict[str, Any]:
    """Passthrough; the capped Send fan-out is the conditional edge after this."""
    spec = registry.get(state.archetype_id)
    chosen = spec.channels[: spec.fanout_cap]
    return {"step_log": [f"draft_dispatch: fan-out {len(chosen)} channel(s) "
                         f"(cap={spec.fanout_cap}): {', '.join(c.value for c in chosen)}"]}


def _draft_fanout(state: CampaignState):
    """B7 fan-out: one Send per channel, HARD-capped at fanout_cap. Worker logic is
    FIXED; only cardinality + content vary."""
    from langgraph.types import Send

    spec = registry.get(state.archetype_id)
    chosen = spec.channels[: spec.fanout_cap]
    return [
        Send("draft_one", {
            "campaign_id": state.campaign_id, "run_id": state.run_id,
            "tenant_id": state.tenant_id, "archetype_id": state.archetype_id,
            "brief": state.brief, "strategy_text": state.strategy_text,
            "research_text": state.research_text,
            "channel": c.value,
        })
        for c in chosen
    ]


def _make_draft_one_node(team_store):
    def _draft_one_node(state: Any) -> dict[str, Any]:
        """REAL per-channel draft (content-brief cell, Sonnet). One Send worker.

        LangGraph hands a Send worker its raw ``Send.arg`` dict (not coerced to the
        schema), so normalize to :class:`CampaignState` first."""
        from cells.content_brief import build_content_brief_cell

        if not isinstance(state, CampaignState):
            state = CampaignState.model_validate(state)
        # `channel` rides in on the Send payload; workers never write it back.
        channel = state.channel or "ig"
        cell = build_content_brief_cell()
        prompt = (
            f"Campaign brief: {state.brief or state.archetype_id}\n"
            f"Strategy:\n{state.strategy_text}\n"
            f"Produce one organic post for channel '{channel}'. Caption in the brand voice."
        )
        brief_out = cell.run_sync(prompt)
        asset_id = f"as_{uuid.uuid4().hex[:16]}"
        asset = {
            "id": asset_id, "asset_type": f"post:{channel}", "channel": channel,
            "content": brief_out.model_dump(),
        }
        # Persist the artifact HELD at production time (status='queued', never
        # 'sent') so the downstream critic FK + the queue confirm have a row.
        if team_store is not None:
            from team.store import ASSET_STATUS_QUEUED
            team_store.record_asset(
                id=asset_id, campaign_id=state.campaign_id,
                asset_type=f"post:{channel}", content=brief_out.model_dump(),
                status=ASSET_STATUS_QUEUED,
            )
        _record_run(team_store, campaign_id=state.campaign_id, run_id=state.run_id,
                    role="draft", model=cell.model if isinstance(cell.model, str) else str(cell.model),
                    inp={"channel": channel}, out=brief_out.model_dump())
        return {"assets": [asset], "step_log": [f"draft[{channel}]: real caption produced"]}

    return _draft_one_node


def _make_critique_node(team_store):
    def _critique_node(state: CampaignState) -> dict[str, Any]:
        """B8: independent critic pass per asset (REAL critic cell). Never a debate."""
        from cells.critic import build_critic_cell

        cell = build_critic_cell()
        crits: list[dict[str, Any]] = []
        for asset in state.assets:
            content = asset.get("content", {})
            caption = content.get("caption") or content.get("headline") or str(content)
            prompt = (
                f"Campaign objective: {state.strategy_text or state.archetype_id}\n"
                f"Channel: {asset.get('channel')}\n"
                f"ASSET TO JUDGE (caption):\n{caption}\n"
                f"Headline: {content.get('headline','')}\nCTA: {content.get('call_to_action','')}"
            )
            crit = cell.run_sync(prompt)
            row = {"asset_id": asset["id"], "verdict": crit.verdict.value,
                   "confidence": float(crit.confidence), "rationale": crit.rationale}
            crits.append(row)
            if team_store is not None:
                team_store.record_critique(
                    id=f"cr_{uuid.uuid4().hex[:16]}", asset_id=asset["id"],
                    critic_model=cell.model if isinstance(cell.model, str) else str(cell.model),
                    rationale=crit.rationale, verdict=crit.verdict.value,
                )
            _record_run(team_store, campaign_id=state.campaign_id, run_id=state.run_id,
                        role="critic", model=cell.model if isinstance(cell.model, str) else str(cell.model),
                        inp={"asset_id": asset["id"]}, out=row)
        return {"critiques": crits, "step_log": [f"critique: {len(crits)} independent pass(es)"]}

    return _critique_node


def _make_route_node(team_store, dsn):
    def _route_node(state: CampaignState) -> dict[str, Any]:
        """B9 jury aggregate -> B10 pure-code route (HELD) -> B11/B13 PENDING rows.

        The verdict is NOT a model emission: it is ``harness.router.route`` with the
        channel autonomy dial pinned to HOLD (approve-first). Every asset becomes a
        PENDING ``actions`` row — nothing sends."""
        from cells.base import DEFAULT_MODEL
        from harness.router import route
        from harness.state import AutonomyMode, RouteDecision

        confs = [c["confidence"] for c in state.critiques] or [0.0]
        agg = sum(confs) / len(confs)  # offline jury aggregate (B9): mean critic confidence
        decision = route(agg, autonomy=AutonomyMode.HOLD)  # HOLD -> REVIEW, never AUTO
        assert decision is RouteDecision.REVIEW, "Phase A must HOLD (approve-first)"

        _record_run(team_store, campaign_id=state.campaign_id, run_id=state.run_id,
                    role="jury", model=DEFAULT_MODEL,
                    inp={"confidences": confs}, out={"aggregate": agg, "decision": decision.value})

        pending: list[str] = []
        try:
            from actions import store as actions_store
            for asset in state.assets:
                content = asset.get("content", {})
                draft = content.get("caption") or content.get("headline") or ""
                channel = asset.get("channel", "ig")
                aid = actions_store.record_pending_action(
                    tenant_id=state.tenant_id, decision_id=None, type="post",
                    channel=channel, worker="team", target=None, draft=str(draft),
                    subject=content.get("headline"), conf=agg, threshold=0.85,
                    esc_kind="hold", esc_label="approve-first (Phase A)",
                    idempotency_key=f"{state.run_id}:{asset['id']}", run_id=state.run_id,
                    dsn=dsn,
                )
                pending.append(aid)
            note = f"route: decision={decision.value}; {len(pending)} action(s) PENDING (HELD)"
        except Exception as exc:
            note = f"route: decision={decision.value}; actions store unavailable ({type(exc).__name__})"
        return {"pending_action_ids": pending, "step_log": [note]}

    return _route_node


def _make_queue_node(team_store):
    def _queue_node(state: CampaignState) -> dict[str, Any]:
        """B11 HELD enqueue: status='queued' only — never 'sent'."""
        from team.store import ASSET_STATUS_QUEUED

        queued: list[str] = []
        for asset in state.assets:
            if team_store is not None:
                team_store.record_asset(
                    id=asset["id"], campaign_id=state.campaign_id,
                    asset_type=asset.get("asset_type", "post"),
                    content=asset.get("content", {}), status=ASSET_STATUS_QUEUED,
                )
            queued.append(asset["id"])
        return {"queued_asset_ids": queued,
                "step_log": [f"queue: enqueued {len(queued)} asset(s) HELD (approve-first)"]}

    return _queue_node


# --------------------------------------------------------------------------- #
# Graph construction — FIXED shape, compiled once.
# --------------------------------------------------------------------------- #


def build_campaign_graph(*, team_store=None, dsn: str | None = None, checkpointer=None):
    """Compile the wired campaign spine. Shape is fixed; the spec only toggles the
    research segment + parameterizes channels/fanout_cap.

    The toggle (B2 research on/off) is the ``route_archetype`` conditional edge after
    ``plan`` — the model never participates in this routing.
    """
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph

    b = StateGraph(CampaignState)
    b.add_node("plan", _plan_node)
    b.add_node("research", _make_research_node(team_store, dsn))
    b.add_node("strategy", _make_strategy_node(team_store))
    b.add_node("draft_dispatch", _draft_dispatch_node)
    b.add_node("draft_one", _make_draft_one_node(team_store))
    b.add_node("critique", _make_critique_node(team_store))
    b.add_node("route", _make_route_node(team_store, dsn))
    b.add_node("queue", _make_queue_node(team_store))

    b.add_edge(START, "plan")
    # Pure-code toggle: route past B2 research when the spec disables it.
    b.add_conditional_edges(
        "plan", lambda s: router.route_archetype(s, after="plan"),
        {"research": "research", "strategy": "strategy"},
    )
    b.add_edge("research", "strategy")
    b.add_edge("strategy", "draft_dispatch")
    # Capped Send fan-out (B7): cardinality varies, worker logic is fixed.
    b.add_conditional_edges("draft_dispatch", _draft_fanout, ["draft_one"])
    b.add_edge("draft_one", "critique")
    b.add_edge("critique", "route")
    b.add_edge("route", "queue")
    b.add_edge("queue", END)

    return b.compile(checkpointer=checkpointer or InMemorySaver())


def run_campaign(
    *, archetype_id: str, tenant_id: str, brief: str = "",
    campaign_id: str | None = None, dsn: str | None = None, persist: bool = True,
    run_id: str | None = None,
) -> CampaignState:
    """Classify-free direct run for a KNOWN archetype id, in-process to completion.

    Use ``archetypes.classify.classify_brief`` first to get the id from a brief; this
    runs the wired spine for that id. Real cells run; nothing sends (all PENDING/HELD).

    ``run_id`` may be supplied so a caller (e.g. the studio's async run endpoint) knows
    the id BEFORE the run finishes and can poll the per-role ``agent_runs`` as they land.
    """
    if archetype_id not in registry.REGISTRY:
        raise KeyError(f"unregistered archetype {archetype_id!r}; registry={registry.ids()}")
    dsn = dsn or os.environ.get("ENGINE_DATABASE_URL") \
        or "postgresql://scalers:scalers@localhost:5432/scalers"
    team_store = None
    if persist:
        from team.store import TeamStore
        team_store = TeamStore(dsn)
        team_store.setup()
        try:
            from actions import store as actions_store
            actions_store.ensure_schema(dsn)
        except Exception:
            pass

    campaign_id = campaign_id or f"camp_{uuid.uuid4().hex[:12]}"
    run_id = run_id or f"team-{campaign_id}-{uuid.uuid4().hex[:12]}"
    graph = build_campaign_graph(team_store=team_store, dsn=dsn)
    init = CampaignState(
        campaign_id=campaign_id, run_id=run_id, tenant_id=tenant_id,
        archetype_id=archetype_id, brief=brief,
    )
    final = graph.invoke(init, config={"configurable": {"thread_id": run_id}})
    return CampaignState.model_validate(final)
