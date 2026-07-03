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
import re
import uuid
from typing import Annotated, Any

from pydantic import BaseModel, Field

from archetypes import registry, router


def _brand_voice_block(tenant_id: str) -> str:
    """The tenant's REAL resolved brand voice (tone / structure / lexicon / bans) +
    the approved-claims allow-list, formatted for a cell prompt so the content draft
    is WRITTEN in it and the critic JUDGES against it.

    Resolved from the per-tenant pack via ``resolve_brand_voice`` (the same source the
    supervisor instruction + the per-lead copywriter use). Degrades to ``""`` honestly
    when the pack / dimensions cannot be resolved — the cell then writes from the brief
    alone, never a fabricated voice. Lazy import keeps the archetypes package free of a
    studio import at module load."""
    try:
        from studio.customer_research import resolve_brand_voice

        voice, claims = resolve_brand_voice(tenant_id)
    except Exception:
        return ""
    if not voice.strip():
        return ""
    lines = [
        "# BRAND VOICE — the studio's own voice; write/judge IN this voice:",
        voice.strip(),
    ]
    if claims:
        lines += [
            "",
            "# APPROVED CLAIMS — the ONLY factual / credential / offer claims allowed "
            "(anything else is off-voice and must be flagged, never asserted):",
            *(f"- {c}" for c in claims),
        ]
    return "\n".join(lines)


def _documents_block(
    tenant_id: str, query: str, *, k: int = 4, dsn: str | None = None
) -> tuple[str, list[dict[str, Any]]]:
    """Retrieve the top-k passages from the tenant's PERSISTENT document store relevant
    to ``query`` (ts_rank), formatted as a cell-prompt block, plus the list of passages
    actually used (``[{document, heading, document_id}]``) for evidence.

    Each node pulls the passages relevant to ITS task (the copywriter pulls voice/CTA,
    the critic pulls the guardrails) so the large playbook never blows the context
    window. Degrades to ``("", [])`` honestly when there is no active doc / no lexical
    match — only genuinely retrieved passages are ever recorded. Lazy import keeps the
    archetypes package free of a studio import at module load."""
    try:
        from studio.documents import retrieve

        hits = retrieve(tenant_id, query, k=k, dsn=dsn)
    except Exception:
        return "", []
    if not hits:
        return "", []
    lines = [
        "# TENANT KNOWLEDGE — relevant passages from the operator's uploaded documents "
        "(ground claims in these; cite by document + section, never invent beyond them):"
    ]
    used: list[dict[str, Any]] = []
    for h in hits:
        head = (h.get("heading") or "").strip()
        doc = h.get("doc_name") or "document"
        cite = f"{doc} › {head}" if head else doc
        lines.append(f"- [{cite}] {(h.get('content') or '')[:600]}")
        used.append({"document": doc, "heading": head or None, "document_id": h.get("document_id")})
    return "\n".join(lines), used


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
    # Operator-chosen run parameters carried in from the interview-gated plan.
    # ``force_research`` turns the B2 web-research node ON even for an archetype that
    # toggles it off by default (the operator answered "deep research: yes"); the
    # router shape is untouched — the compose conditional edge just selects 'research'.
    # ``output_count`` sizes the draft fan-out (produce exactly N drafts, capped) so a
    # plan that asks for 10 drafts gets 10, not the per-channel default.
    force_research: bool = False
    output_count: int = 0
    # Per-worker channel, set on the Send payload for a draft_one worker. Workers
    # never write it back, so there is no concurrent-write conflict at fan-in.
    channel: str = ""
    # Per-worker DRAFT VARIATION (wwy.8): so N requested drafts are N DISTINCT
    # drafts, each same-channel Send carries a distinct variant slot (index within
    # its channel group, of the total for that channel) and, when the strategy
    # exposes them, a distinct key-message angle. Ride on the Send payload like
    # ``channel``; workers never write them back.
    variant_index: int = 0
    variant_total: int = 1
    variant_angle: str = ""
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
    # When the operator forced deep research on a B2-off archetype, reflect the real
    # executed path (plan -> research -> …) in the log so it matches what runs.
    if getattr(state, "force_research", False) and "research" not in path:
        path = [path[0], "research", *path[1:]]
    return {"step_log": [f"plan[{spec.id}]: {' -> '.join(path)}"]}


def _make_research_node(team_store, dsn):
    def _research_node(state: CampaignState) -> dict[str, Any]:
        """REAL research pipeline; honest-empty (no fabrication) when no provider key.

        ALWAYS records a ``researcher`` agent_run — on success it carries the real
        cited ``sources`` ([{url,title,snippet}] verbatim from the provider) so the
        agency rail can show which URLs were used; on a degrade/failure it records an
        honest ``degraded`` run with the real reason (never a fabricated source, and
        never a SILENT skip that would leave the UI stuck 'queued'). The only time NO
        researcher run is written is when this node is not visited at all — which the
        UI reads as 'research not required for this campaign'."""
        from cells.base import DEFAULT_MODEL

        # ``map_market`` is the demand/pain/angle research intent (research.adapter
        # `Intent` is a Literal — its members are map_market / find_communities /
        # competitor_creatives; there is NO `DEMAND`, so the prior `Intent.DEMAND`
        # raised AttributeError and silently degraded EVERY research run before it
        # ever reached Firecrawl. Use the real intent so the live provider is called.
        inp = {"intent": "map_market", "tenant": state.tenant_id}
        try:
            from research.adapter import ResearchQuery
            from research.pipeline import gather_and_persist, live_registry
            from research.router import ResearchRouter

            reg = live_registry()  # firecrawl/exa enabled only when key present
            r = ResearchRouter(list(reg.values()))
            query = ResearchQuery(
                intent="map_market", niche="tattoo studio", tenant_id=state.tenant_id,
            )
            result, ids = gather_and_persist(
                r, query, run_id=state.run_id, tenant_id=state.tenant_id, dsn=dsn,
            )
            cited = [dict(x) for x in (getattr(result, "sources_cited", []) or [])]
            # Verbatim provider hits the rail renders (real URLs only — drop urlless).
            sources = [
                {"url": s.get("url"), "title": s.get("title"), "snippet": s.get("snippet")}
                for s in cited
                if s.get("url")
            ][:8]
            text = "\n".join(f"- {s.get('title') or s.get('url')}" for s in cited)
            note = (f"research: {len(cited)} cited source(s), {len(ids)} persisted"
                    if cited else
                    "research: ran, no live citations returned (keyless/no hits); 0 citations, no fabrication")
            _record_run(team_store, campaign_id=state.campaign_id, run_id=state.run_id,
                        role="researcher", model=DEFAULT_MODEL, inp=inp,
                        out={"cited": len(cited), "persisted": len(ids), "sources": sources})
            return {"research_text": text, "step_log": [note]}
        except Exception as exc:  # never fabricate, never silently skip: record honest failure
            _record_run(team_store, campaign_id=state.campaign_id, run_id=state.run_id,
                        role="researcher", model=DEFAULT_MODEL, inp=inp,
                        out={"cited": 0, "persisted": 0, "sources": [],
                             "degraded": True, "error": f"{type(exc).__name__}: {exc}"})
            return {"step_log": [f"research: degraded ({type(exc).__name__}); 0 citations, no fabrication"]}

    return _research_node


def _make_strategy_node(team_store, dsn=None):
    def _strategy_node(state: CampaignState) -> dict[str, Any]:
        """REAL strategy cell (Sonnet, temp 0) -> CampaignStrategy."""
        from cells.strategy import build_strategy_cell, build_strategy_prompt, render_strategy

        cell = build_strategy_cell()
        prompt = build_strategy_prompt(
            state.tenant_id, state.brief or f"{state.archetype_id} campaign",
            research=state.research_text or None,
        )
        # Ground the strategy in the operator's own documents — pull the passages
        # relevant to positioning/angle/audience so the angle reflects the playbook.
        docs_block, _docs_used = _documents_block(
            state.tenant_id,
            f"{state.brief} strategy positioning angle audience offer",
            k=4, dsn=dsn,
        )
        if docs_block:
            prompt = docs_block + "\n\n" + prompt
        strategy = cell.run_sync(prompt)
        text = render_strategy(strategy)
        _record_run(team_store, campaign_id=state.campaign_id, run_id=state.run_id,
                    role="strategist", model=cell.model if isinstance(cell.model, str) else str(cell.model),
                    inp={"brief": state.brief}, out=strategy.model_dump())
        return {"strategy_text": text, "step_log": [f"strategy: angle set ({len(text)} chars)"]}

    return _strategy_node


# A hard ceiling on how many drafts one run can fan out, independent of the plan, so
# an absurd interview answer ("make 5000 emails") can never spawn a runaway of real
# model calls. The interview-chosen ``output_count`` is honored up to this cap.
_OUTPUT_HARD_CAP = 12


def _planned_channels(state: CampaignState) -> list[str]:
    """The ordered channel list the draft fan-out will actually produce.

    Default (no ``output_count``): one draft per spec channel, capped at fanout_cap —
    the original behavior. With ``output_count`` set (the operator asked for N drafts
    in the interview): exactly N drafts, round-robined across the spec's channels and
    bounded by :data:`_OUTPUT_HARD_CAP`. So "10 emails" on an email-only plan yields
    10 email drafts; "10" across IG+email yields 5 + 5."""
    spec = registry.get(state.archetype_id)
    chosen = [c.value for c in spec.channels[: spec.fanout_cap]] or ["ig"]
    n = state.output_count if (state.output_count and state.output_count > 0) else len(chosen)
    n = max(1, min(int(n), _OUTPUT_HARD_CAP))
    return [chosen[i % len(chosen)] for i in range(n)]


def _draft_dispatch_node(state: CampaignState) -> dict[str, Any]:
    """Passthrough; the capped Send fan-out is the conditional edge after this."""
    chans = _planned_channels(state)
    return {"step_log": [f"draft_dispatch: fan-out {len(chans)} draft(s): {', '.join(chans)}"]}


_KEY_MESSAGE_RE = re.compile(r"^\s*-\s+(.+?)\s*$", re.MULTILINE)


def _extract_key_messages(strategy_text: str) -> list[str]:
    """The strategy's key-message bullets, parsed from the rendered strategy text
    (``cells.strategy`` renders each as ``  - {message}``). These become the
    per-variant angles so each same-channel draft leads with a DIFFERENT message.
    Empty list when the strategy has no bullets — the variant-index directive
    alone still forces distinctness."""
    return [m.strip() for m in _KEY_MESSAGE_RE.findall(strategy_text or "") if m.strip()]


def _draft_fanout(state: CampaignState):
    """B7 fan-out: one Send per planned draft, HARD-capped. Worker logic is FIXED;
    only cardinality + content vary (cardinality driven by the agreed plan).

    Each Send also carries a DISTINCT variant slot (wwy.8): same-channel drafts are
    numbered ``index of total`` within their channel group and assigned a distinct
    key-message angle, so N requested drafts produce N distinct prompts."""
    from langgraph.types import Send

    channels = _planned_channels(state)
    angles = _extract_key_messages(state.strategy_text)
    per_channel_total = {ch: channels.count(ch) for ch in set(channels)}
    seen: dict[str, int] = {}
    sends = []
    for i, channel in enumerate(channels):
        idx = seen.get(channel, 0)
        seen[channel] = idx + 1
        sends.append(Send("draft_one", {
            "campaign_id": state.campaign_id, "run_id": state.run_id,
            "tenant_id": state.tenant_id, "archetype_id": state.archetype_id,
            "brief": state.brief, "strategy_text": state.strategy_text,
            "research_text": state.research_text,
            "channel": channel,
            "variant_index": idx,
            "variant_total": per_channel_total[channel],
            "variant_angle": angles[i % len(angles)] if angles else "",
        }))
    return sends


def _make_draft_one_node(team_store, dsn=None):
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
        # Ground the draft in the tenant's REAL resolved brand voice (tone / lexicon /
        # bans / approved claims) instead of merely telling the cell "in the brand
        # voice" with no voice attached. Honest-empty when the pack can't resolve.
        voice_block = _brand_voice_block(state.tenant_id)
        # Retrieve the playbook passages relevant to THIS draft (voice/angle/CTA for the
        # channel) so the copy is grounded in the operator's own documents. The used
        # passages are recorded on the draft run for the evidence chips.
        docs_block, docs_used = _documents_block(
            state.tenant_id,
            f"{state.brief} {state.strategy_text} {channel} voice tone hook call to action",
            k=3, dsn=dsn,
        )
        # Per-worker VARIATION directive (wwy.8): when this channel produced more
        # than one draft, each Send gets a distinct slot so N requested drafts are
        # N DISTINCT drafts — a different hook/opening and value-prop angle per
        # variant (deterministic; same-channel prompts differ). The fan-in dedupe
        # in _route_node is the backstop if the model still repeats.
        variant_directive = ""
        if state.variant_total > 1:
            variant_directive = (
                f"VARIANT {state.variant_index + 1} OF {state.variant_total} for channel "
                f"'{channel}': this MUST be a DISTINCT draft — use a different hook and "
                f"opening structure AND a different value-proposition angle from the other "
                f"{state.variant_total} variants. Never reuse an opening line."
            )
            if state.variant_angle:
                variant_directive += f" Lead with this angle: {state.variant_angle}."
        prompt = "\n".join(
            p for p in [
                voice_block,
                docs_block,
                f"Campaign brief: {state.brief or state.archetype_id}",
                f"Strategy:\n{state.strategy_text}",
                variant_directive,
                f"Produce one organic post for channel '{channel}'. Write the caption "
                "in the BRAND VOICE above" + (
                    ", using ONLY the approved claims." if voice_block
                    else " for this studio."
                ),
            ] if p
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
                    inp={"channel": channel, "tenant_id": state.tenant_id,
                         "brand_voice_applied": bool(voice_block),
                         "documents_used": docs_used},
                    out=brief_out.model_dump())
        return {"assets": [asset], "step_log": [f"draft[{channel}]: real caption produced"]}

    return _draft_one_node


def _make_critique_node(team_store, dsn=None):
    def _critique_node(state: CampaignState) -> dict[str, Any]:
        """B8: independent critic pass per asset (REAL critic cell). Never a debate."""
        from cells.critic import build_critic_cell

        cell = build_critic_cell()
        # Give the critic the SAME real brand voice + approved-claims the draft was
        # written in, so "brand-voice mismatch" / "unapproved claim" are judged against
        # the loaded voice, not the critic's generic prior. Resolved once per run.
        voice_block = _brand_voice_block(state.tenant_id)
        # Pull the playbook's GUARDRAILS passages so the critic judges against the
        # operator's own do-not-say / compliance rules, not just a generic prior.
        docs_block, _ = _documents_block(
            state.tenant_id,
            "guardrails do-not-say banned words unapproved claims compliance brand voice",
            k=3, dsn=dsn,
        )
        crits: list[dict[str, Any]] = []
        for asset in state.assets:
            content = asset.get("content", {})
            caption = content.get("caption") or content.get("headline") or str(content)
            prompt = "\n".join(
                p for p in [
                    voice_block,
                    docs_block,
                    f"Campaign objective: {state.strategy_text or state.archetype_id}",
                    f"Channel: {asset.get('channel')}",
                    f"ASSET TO JUDGE (caption):\n{caption}",
                    f"Headline: {content.get('headline','')}",
                    f"CTA: {content.get('call_to_action','')}",
                    (
                        "Judge whether the asset is IN the brand voice above and uses "
                        "ONLY the approved claims; flag any off-voice phrase, banned "
                        "lexicon, or unapproved claim as a concrete issue."
                        if voice_block else ""
                    ),
                ] if p
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

        # Fan-in DEDUPE (wwy.8): N requested drafts must be N DISTINCT drafts. The
        # per-worker variation directive makes prompts differ; this is the backstop
        # if the model still repeats. Normalize each caption (same normalizer the
        # copywriter's anti-over-templating validator uses) and SKIP a duplicate
        # with a concrete reason BEFORE staging — a repeated draft never becomes a
        # second PENDING row.
        from cells.copywriter import _normalize

        pending: list[str] = []
        skips: list[str] = []
        seen: dict[str, str] = {}
        try:
            from actions import store as actions_store
            for asset in state.assets:
                content = asset.get("content", {})
                draft = content.get("caption") or content.get("headline") or ""
                norm = _normalize(str(draft))
                if norm and norm in seen:
                    skips.append(
                        f"SKIPPED duplicate draft (asset {asset['id']}) — normalized "
                        f"caption matches asset {seen[norm]}"
                    )
                    continue
                channel = asset.get("channel", "ig")
                aid = actions_store.record_pending_action(
                    tenant_id=state.tenant_id, decision_id=None, type="post",
                    channel=channel, worker="team", target=None, draft=str(draft),
                    subject=content.get("headline"), conf=agg, threshold=0.85,
                    esc_kind="hold", esc_label="approve-first (Phase A)",
                    idempotency_key=f"{state.run_id}:{asset['id']}", run_id=state.run_id,
                    dsn=dsn,
                )
                if norm:
                    seen[norm] = asset["id"]
                pending.append(aid)
            note = (
                f"route: decision={decision.value}; {len(pending)} action(s) PENDING (HELD)"
                + (f"; {len(skips)} duplicate(s) skipped" if skips else "")
            )
        except Exception as exc:
            note = f"route: decision={decision.value}; actions store unavailable ({type(exc).__name__})"
        return {"pending_action_ids": pending, "step_log": [note, *skips]}

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


def _after_plan(state: CampaignState) -> str:
    """Select the node after ``plan``: the spec-driven route, OR 'research' when the
    operator forced deep research on an archetype that toggles it off. Returns only a
    pre-declared spine node ('research' | 'strategy') — never invents topology."""
    nxt = router.route_archetype(state, after="plan")  # 'research' or 'strategy'
    if nxt != "research" and getattr(state, "force_research", False):
        return "research"
    return nxt


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
    b.add_node("strategy", _make_strategy_node(team_store, dsn))
    b.add_node("draft_dispatch", _draft_dispatch_node)
    b.add_node("draft_one", _make_draft_one_node(team_store, dsn))
    b.add_node("critique", _make_critique_node(team_store, dsn))
    b.add_node("route", _make_route_node(team_store, dsn))
    b.add_node("queue", _make_queue_node(team_store))

    b.add_edge(START, "plan")
    # Pure-code toggle: route past B2 research when the spec disables it — UNLESS the
    # operator explicitly requested deep research (``force_research``), in which case
    # we select the same pre-declared 'research' node. The router's spec-driven shape
    # is unchanged; this only flips the toggle ON when the plan asks for it.
    b.add_conditional_edges(
        "plan", _after_plan,
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


def _run_guarded(graph, init: "CampaignState", run_id: str) -> "CampaignState":
    """Invoke the campaign graph under ``thread_id=run_id`` with the fk5 replay
    guard ported from ``harness.graph.CompiledGraph.run``.

    CampaignState's ``assets`` / ``critiques`` / ``queued_asset_ids`` /
    ``pending_action_ids`` / ``step_log`` are ``operator.add`` (append-reduced)
    channels, so blindly re-invoking a COMPLETED ``run_id`` would replay the nodes
    and re-append to those channels — doubling the staged actions / assets (the
    fk5 hazard). The guard inspects the durable snapshot first:

    * completed thread (values present, no pending ``next``)  -> return the
      persisted state, never re-invoke (no re-accumulation, no side-effect re-fire);
    * crashed mid-run (values present, ``next`` pending)       -> resume from the
      last checkpoint via ``invoke(None, ...)`` (only pending nodes re-run);
    * fresh thread (no durable values)                         -> normal invoke.

    Uses the SYNC ``get_state`` — valid here because the campaign path runs a sync
    ``graph.invoke`` over a sync ``PostgresSaver`` (or ``InMemorySaver``); the async
    ``AsyncPostgresSaver`` is only for the async harness spine (its sync methods
    raise under the async Pregel loop, and vice-versa)."""
    cfg = {"configurable": {"thread_id": run_id}}
    snapshot = graph.get_state(cfg)
    if snapshot.values:
        if snapshot.next:  # crashed mid-run -> resume; only pending nodes re-run
            final = graph.invoke(None, cfg)
        else:              # completed -> persisted state, never replay (fk5)
            final = snapshot.values
    else:                  # fresh thread
        final = graph.invoke(init, cfg)
    return CampaignState.model_validate(final)


def run_campaign(
    *, archetype_id: str, tenant_id: str, brief: str = "",
    campaign_id: str | None = None, dsn: str | None = None, persist: bool = True,
    run_id: str | None = None, force_research: bool = False, output_count: int = 0,
    checkpointer=None,
) -> CampaignState:
    """Classify-free direct run for a KNOWN archetype id, in-process to completion.

    Use ``archetypes.classify.classify_brief`` first to get the id from a brief; this
    runs the wired spine for that id. Real cells run; nothing sends (all PENDING/HELD).

    ``run_id`` may be supplied so a caller (e.g. the studio's async run endpoint) knows
    the id BEFORE the run finishes and can poll the per-role ``agent_runs`` as they land.

    DURABILITY (fr1.2): when ``ENGINE_DATABASE_URL`` is set the graph is compiled with
    the SYNC durable ``PostgresSaver`` (checkpointed under ``thread_id=run_id``), so a
    crash mid-campaign resumes at the last completed node on restart instead of losing
    the run. Callers may inject their own ``checkpointer`` (tests / a shared saver);
    when unset and no env DB is configured the in-memory saver keeps the old ephemeral
    behavior. Either way the invoke goes through the fk5 completed-thread replay guard.
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
    init = CampaignState(
        campaign_id=campaign_id, run_id=run_id, tenant_id=tenant_id,
        archetype_id=archetype_id, brief=brief,
        force_research=force_research, output_count=output_count,
    )

    # A caller-supplied checkpointer owns its own lifecycle (don't manage it here).
    if checkpointer is not None:
        graph = build_campaign_graph(team_store=team_store, dsn=dsn, checkpointer=checkpointer)
        return _run_guarded(graph, init, run_id)

    # Env-var seam (AC1 parity): ENGINE_DATABASE_URL flips on the durable sync saver.
    from harness.config import get_settings

    database_url = get_settings().database_url
    if database_url:
        from langgraph.checkpoint.postgres import PostgresSaver

        # Managed connection/pool for the life of this run; setup() is idempotent
        # (creates the LangGraph checkpoint tables once).
        with PostgresSaver.from_conn_string(database_url) as cp:
            cp.setup()
            graph = build_campaign_graph(team_store=team_store, dsn=dsn, checkpointer=cp)
            return _run_guarded(graph, init, run_id)

    # No durable DB configured -> in-memory saver (unchanged ephemeral behavior).
    graph = build_campaign_graph(team_store=team_store, dsn=dsn)
    return _run_guarded(graph, init, run_id)
