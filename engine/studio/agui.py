"""AG-UI surface for the Campaign Studio (Phase 3.1-backend).

Wraps the EXISTING Studio Host + role cells as an AG-UI agent, mounted on the
engine's FastAPI ``main:app`` ALONGSIDE the strawberry ``/graphql`` + SSE — a new
route ``POST /studio/agui`` driven by ``pydantic_ai.ui.ag_ui.AGUIAdapter``.

What is REAL here (honesty gate — real model calls + real persistence only):

* **Shared state** — the campaign plan is an AG-UI shared state: :class:`CampaignPlan`
  (a Pydantic model) carried by :class:`StudioDeps` (a ``StateHandler``). The
  operator edits a field on the frontend → the adapter validates the inbound
  ``state`` into ``deps.state`` → the host re-plans using the changed input. The
  ``revise_plan`` tool mutates that state, PERSISTS it to ``campaign_plans``, and
  emits an AG-UI ``StateSnapshotEvent`` so the change syncs back. Bidirectional.
* **Role brainstorm** — ``brainstorm_with_roles`` runs the EXISTING typed cells for
  real: funnel-architect (Sonnet) → copywriter (Sonnet) → critic (Sonnet, a real
  INDEPENDENT pass, not a staged debate) → a jury verdict (Opus). Each emits a
  LABELED in-thread message and is logged to ``studio_chat_turns`` carrying its
  real model pin. No canned brainstorm.
* **Approval gate** — ``stage_publish`` is marked ``requires_approval=True``. The
  model can REQUEST it, but pydantic-ai surfaces it as an unapproved
  ``DeferredToolRequests`` — it NEVER auto-fires. Only after the operator approves
  does the body run, and even then it merely STAGES a PENDING row in the existing
  ``actions`` store (status='pending') behind the existing HOLD. Nothing is sent.

Models (task pins): host turn = Haiku 4.5 (cheap, conversational); the role cells
keep their own pins; jury = Opus 4.8.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request
from pydantic import BaseModel, Field
from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.ui import StateDeps  # noqa: F401  (re-exported for callers/tests)

from studio.campaign_plan_store import latest_plans, upsert_plan
from studio.chat_store import VALID_ROLES, PostgresChatStore

# --------------------------------------------------------------------------- #
# Model pins (task §HONESTY GATE)
# --------------------------------------------------------------------------- #
# Haiku 4.5 for the fast conversational Studio Host turn. The repo pins the alias
# (harness.config.DEFAULT_HAIKU = "claude-haiku-4-5"); the dated build the task
# names (claude-haiku-4-5-20251001) resolves to the same model.
HOST_AGUI_MODEL = "anthropic:claude-haiku-4-5"
JURY_MODEL = "anthropic:claude-opus-4-8"  # harness.config.DEFAULT_OPUS


# --------------------------------------------------------------------------- #
# Shared state: the editable campaign plan
# --------------------------------------------------------------------------- #


class CampaignPlan(BaseModel):
    """The AG-UI SHARED STATE for one studio session.

    Synced bidirectionally via :class:`StateDeps`/:class:`StudioDeps`: the operator
    edits any field on the frontend and the host re-plans from the changed input;
    the host edits it through ``revise_plan`` and the change snaps back to the UI.
    """

    goal: str = ""
    audience: str = ""
    channels: list[str] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)
    tasks_per_role: dict[str, list[str]] = Field(default_factory=dict)
    assets: list[dict[str, Any]] = Field(default_factory=list)
    schedule: dict[str, str] = Field(default_factory=dict)
    # Operator-supplied brand / strategy notes (e.g. an uploaded notes file). This is
    # REAL context the host reads on every turn (see ``_plan_context``) and that the
    # run loads with the plan — not a badge. Free text, persisted with the plan.
    notes: str = ""
    # --- interview-gathered run parameters (Agency-page scoping gate, P1a) -------- #
    # Collected by the supervisor interview BEFORE a run may start (studio.interview).
    # They make the run match what the operator agreed to: output_count sizes the draft
    # fan-out (P2), deep_research forces the web-research node ON (P1b), campaign_type
    # selects a matching archetype, and action_type / lead_count / tone / drafts_only
    # refine the posture. Defaults mean "unset" (the interview asks for them).
    output_count: int = 0
    action_type: str = ""
    lead_count: int = 0
    tone: str = ""
    campaign_type: str = ""
    deep_research: bool | None = None
    drafts_only: bool | None = None
    # Lead source (hard compliance branch): "provided" = use ONLY the operator's own
    # leads (uploaded CSV / existing DB), researched per-lead; "source_new" = find new
    # prospects on the web. Empty = not chosen yet. Drives the orchestration mode.
    lead_source: str = ""
    # Uploaded customer list — a REAL parse of the operator's CSV ({filename, rows,
    # columns, sample, ingested}). Persisted with the plan and surfaced to the
    # supervisor on every turn (see `_customers_context`) so it can truthfully say
    # "I see your CSV, N rows: col, col" and reason over the rows. Empty = no CSV
    # uploaded (the supervisor must NOT pretend a list exists).
    customers: dict[str, Any] = Field(default_factory=dict)


@dataclass
class StudioDeps:
    """Per-request deps. Implements the AG-UI ``StateHandler`` protocol (it is a
    dataclass with a ``state`` field), so the adapter loads the frontend's state
    into ``state`` at run start. ``session_id``/``dsn`` carry the persistence seam
    the tools write through."""

    state: CampaignPlan = field(default_factory=CampaignPlan)
    session_id: str = "studio-default"
    tenant_id: str = "demo"
    dsn: str | None = None


_SYSTEM = (
    "You are the Studio Host for a marketing Campaign Studio. You co-create an "
    "EDITABLE campaign plan with one operator (a tattoo artist / small studio "
    "owner) and you act through tools — you do not just chat.\n"
    "\n"
    "The campaign plan is SHARED STATE you can read on every turn. Rules:\n"
    "1. Whenever the operator states or changes the goal, audience, channels, "
    "sections, schedule, or tasks — call `revise_plan` with ONLY the changed "
    "fields. This re-plans from their edit and syncs the plan back to them.\n"
    "2. When the operator asks to brainstorm / draft / get the team's take, call "
    "`brainstorm_with_roles` ONCE. That runs the real role cells (funnel "
    "architect, copywriter, an independent critic, and an Opus jury). Do NOT "
    "invent their output yourself.\n"
    "3. When the operator asks to RUN / launch / execute / kick off / 'let's go' on "
    "the campaign (or approves the plan to run), call `run_campaign` ONCE. It runs "
    "the real multi-agent spine (research -> strategy -> drafts -> critique -> "
    "jury) and produces drafts + actions staged for approval; everything is HELD "
    "and NOTHING is sent. The operator can watch each agent's step. Do NOT invent "
    "its output.\n"
    "4. You HAVE a customer database and a persistent memory layer. When the "
    "operator asks you to research / look up / target customers or leads, or refers "
    "to uploaded leads, churn-risk / lapsing customers, or 'these customers', you "
    "MUST call the research tools — NEVER reply that you lack access to a database "
    "or memory. Use `research_lead` to pull ONE lead's grounded facts (interests, "
    "past tattoos, city, persona psychology, prior-campaign memories) and reason "
    "over them. Use `research_and_stage_leads` to research a BATCH (the uploaded "
    "leads or the churn-risk cohort) one-by-one and produce a PERSONALIZED outreach "
    "draft per lead — each draft is staged as a PENDING action in the Review Queue "
    "(HELD, approve-first) and a memory of the outreach is written so you remember "
    "it next time. Reason per-lead before drafting; ground every claim in the facts "
    "the tool returns — never invent a customer detail.\n"
    "5. NEVER send or publish anything yourself. If the operator wants something "
    "posted/emailed, call `stage_publish` — it stages a PENDING action that a "
    "human must approve; it is held, never sent.\n"
    "6. After acting, reply in 2-4 sentences: reflect the current plan and ask 1 "
    "high-leverage clarifying question. Be honest — never claim a tool ran that "
    "did not, never claim anything was sent, and never claim a customer fact the "
    "research tool did not return."
)


# ``output_type`` MUST include ``DeferredToolRequests`` because this agent owns an
# approval-gated tool (``stage_publish``, ``requires_approval=True``). pydantic-ai's
# ``AGUIAdapter.run_stream_native`` only appends ``DeferredToolRequests`` automatically
# when the inbound request carries frontend tools; an AG-UI client that sends no
# frontend tools would otherwise hit "A deferred tool call was present, but
# `DeferredToolRequests` is not among output types" and the approval gate would 500
# instead of surfacing an Approve/Reject interrupt. Declaring it here makes the gate
# work unconditionally (and matches what the hermetic approval test asserts).
# Extended thinking on the Host (Haiku 4.5). Haiku 4.5 supports CLASSIC budget
# thinking only (`anthropic_supports_adaptive_thinking=False` in the installed
# pydantic-ai profile), so the shape is `{"type":"enabled","budget_tokens":N}` with
# 1024 <= N < max_tokens. Extended thinking forces temperature=1 (the prior 0.4 is
# replaced). The reasoning text comes back as a `ThinkingPart` (content + signature)
# and is CAPTURED + persisted in `on_complete` (role="thinking") so a later
# frontend thinking-view can show REAL reasoning. Verified compatible with this
# agent's `output_type=[str, DeferredToolRequests]` + tools (auto tool_choice).
HOST_THINKING_BUDGET = 1024
HOST_MAX_TOKENS = 4096

studio_agent = Agent(
    HOST_AGUI_MODEL,
    deps_type=StudioDeps,
    output_type=[str, DeferredToolRequests],
    instructions=_SYSTEM,
    model_settings={
        "temperature": 1,
        "max_tokens": HOST_MAX_TOKENS,
        "anthropic_thinking": {"type": "enabled", "budget_tokens": HOST_THINKING_BUDGET},
    },
    defer_model_check=True,
)


@studio_agent.instructions
def _plan_context(ctx: RunContext[StudioDeps]) -> str:
    """Surface the live shared-state plan to the host on EVERY turn.

    The static system prompt promises the plan is readable each turn, but the AG-UI
    adapter only loads the inbound ``state`` into ``ctx.deps.state`` — it does NOT
    inject it into the model context. Without this, when the operator edits a plan
    field on the frontend and re-plans, the host can't see the change and asks the
    operator to restate it. This dynamic instruction makes the SHARED STATE (including
    the operator's just-made field edits) genuinely visible, so the host re-plans
    from it instead of asking the operator to repeat themselves."""
    p = ctx.deps.state
    channels = ", ".join(p.channels) if p.channels else "(none yet)"
    sections = ", ".join(p.sections) if p.sections else "(none yet)"
    base = (
        "CURRENT CAMPAIGN PLAN — this is the live SHARED STATE, already reflecting any "
        "edits the operator just made to the plan fields on the frontend. Treat it as "
        "ground truth and re-plan around it; never claim you cannot see it:\n"
        f"- goal: {p.goal or '(empty)'}\n"
        f"- audience: {p.audience or '(empty)'}\n"
        f"- channels: {channels}\n"
        f"- sections: {sections}"
    )
    # Surface uploaded brand / strategy notes as real planning context. The operator
    # attached these (e.g. a brand-voice or strategy file); treat them as ground truth
    # about the brand and weave them into the plan — never invent beyond them.
    if p.notes.strip():
        snippet = p.notes.strip()
        if len(snippet) > 4000:
            snippet = snippet[:4000] + " …[truncated]"
        base += (
            "\n\nOPERATOR BRAND / STRATEGY NOTES (uploaded context — ground every brand "
            "claim in these; do not contradict or invent beyond them):\n" + snippet
        )
    return base


@studio_agent.instructions
def _memory_and_db_context(ctx: RunContext[StudioDeps]) -> str:
    """Advertise the customer DB + memory layer on EVERY turn, and surface the
    memories relevant to this campaign so the Host genuinely 'remembers' prior runs.

    This is what kills the "I don't have access to a memory layer" refusal: the
    capability is now real (the `research_lead` / `research_and_stage_leads` tools),
    and the relevant memories are injected here as ground truth. Recall is best-effort
    — if the memory store is unavailable it degrades to advertising the tools, never
    to a false claim that data exists."""
    p = ctx.deps.state
    lines = [
        "CUSTOMER DATA + MEMORY ARE AVAILABLE TO YOU. You can pull real grounded "
        "facts on any lead (customers + persona traits + tattoo history + prior "
        "campaign memories) and you persist memories across runs. When the operator "
        "asks to research/target customers or references uploaded/churn-risk leads, "
        "CALL `research_lead` or `research_and_stage_leads` — do NOT say you lack "
        "access.",
    ]
    try:
        from memory import MemoryStore

        query = (p.goal or "") + " " + (p.audience or "") or "campaign outreach"
        store = MemoryStore(dsn=ctx.deps.dsn)
        recalled = store.recall(tenant_id=ctx.deps.tenant_id, query=query, k=5)
        if recalled:
            mem_lines = "\n".join(f"  - {m.text}" for m in recalled)
            lines.append(
                "MEMORIES FROM PRIOR CAMPAIGNS/RESEARCH for this studio (treat as "
                "remembered context, not new facts):\n" + mem_lines
            )
        else:
            lines.append(
                "MEMORIES: none recorded yet for this studio (this is your first "
                "research/campaign, or none match) — do not invent any."
            )
    except Exception:
        lines.append(
            "MEMORIES: memory layer present; no memories loaded this turn."
        )
    return "\n".join(lines)


@studio_agent.instructions
def _customers_context(ctx: RunContext[StudioDeps]) -> str:
    """Surface the operator's UPLOADED customer list to the supervisor on EVERY turn.

    This is what lets the supervisor truthfully say "I see your CSV — N rows: name,
    email, city" and reason over the real rows, instead of being blind to the upload.
    The list is a REAL parse of the operator's file (persisted on the plan by
    ``/studio/upload``). HONESTY: when no CSV was uploaded this adds NOTHING — the
    supervisor must not pretend a list exists — and it never invents rows beyond the
    real sample it was given."""
    c = ctx.deps.state.customers or {}
    rows = c.get("rows")
    if not c or not rows:
        return ""  # no CSV uploaded -> say nothing (no fabrication)
    cols = ", ".join(str(x) for x in (c.get("columns") or [])) or "(no header row)"
    lines = [
        "UPLOADED CUSTOMER LIST — the operator uploaded a real CSV and this is a REAL "
        "parse of it. You CAN see this list; when asked how many leads or what columns, "
        "answer from HERE. Treat it as ground truth and never invent rows beyond it:",
        f"- file: {c.get('filename') or 'upload.csv'}",
        f"- rows: {rows}",
        f"- columns: {cols}",
        f"- ingested into the customer DB: {'yes' if c.get('ingested') else 'no (parsed only)'}",
    ]
    sample = c.get("sample") or []
    if sample:
        lines.append("- sample rows (first few, verbatim from the file):")
        for r in sample[:5]:
            if isinstance(r, dict):
                pairs = ", ".join(f"{k}={v}" for k, v in r.items() if str(v).strip())
            else:
                pairs = str(r)
            lines.append(f"    - {pairs[:200]}")
    lines.append(
        "To act on these leads call `research_and_stage_leads`"
        + (" (they are in the customer DB)" if c.get("ingested") else "")
        + " — per-lead, grounded, staged HELD for approval. Nothing is sent."
    )
    return "\n".join(lines)


@studio_agent.instructions
def _brand_voice_context(ctx: RunContext[StudioDeps]) -> str:
    """Tell the supervisor it HAS a brand voice on file and to USE it — never claim it
    lacks one. The studio's own brand voice (tone / structure / preferred + banned
    lexicon + approved claims) is resolved from the tenant pack by
    ``resolve_brand_voice`` and the copywriter cell writes in it. HONESTY: if the pack
    can't be resolved this degrades to stating the brand voice is configured per pack
    and loaded by the copywriter at draft time — it NEVER says 'no brand voice'."""
    try:
        from studio.customer_research import resolve_brand_voice

        brand_voice, _claims = resolve_brand_voice()
        if brand_voice.strip():
            snippet = brand_voice.strip()
            if len(snippet) > 1500:
                snippet = snippet[:1500] + " …[truncated]"
            return (
                "BRAND VOICE IS ON FILE — you have the studio's own brand voice (tone, "
                "structure, preferred + banned lexicon). USE it when drafting; NEVER "
                "claim you have no brand voice. The copywriter cell writes every draft "
                "in this voice:\n" + snippet
            )
    except Exception:
        pass
    return (
        "BRAND VOICE: the studio's brand-voice pack is configured and the copywriter "
        "cell loads it at draft time. You DO have a brand voice — never claim otherwise."
    )


# --------------------------------------------------------------------------- #
# Persistence helpers (sync psycopg, offloaded to threads from the async tools)
# --------------------------------------------------------------------------- #


def _chat_store(dsn: str | None) -> PostgresChatStore:
    from obsapi.db import get_dsn

    store = PostgresChatStore(dsn or get_dsn())
    store.setup()
    return store


def _log_turn(dsn: str | None, session_id: str, role: str, text: str, model: str | None) -> None:
    _chat_store(dsn).append_turn(session_id, role, text, model)


def _persist_plan(dsn: str | None, session_id: str, plan: CampaignPlan) -> str:
    return upsert_plan(session_id, plan.model_dump(), dsn=dsn)


def _extract_thinking(result: Any) -> list[str]:
    """Pull the REAL extended-thinking text (``ThinkingPart.content``) out of a
    completed run. Returns the non-empty reasoning segments in order. Best-effort:
    if the SDK shape changes or no thinking was produced, returns ``[]`` (never
    fabricates a trace)."""
    try:
        from pydantic_ai.messages import ThinkingPart
    except Exception:
        return []
    segments: list[str] = []
    try:
        messages = result.all_messages()
    except Exception:
        return []
    for msg in messages:
        for part in getattr(msg, "parts", []) or []:
            if isinstance(part, ThinkingPart):
                content = (getattr(part, "content", "") or "").strip()
                if content:
                    segments.append(content)
    return segments


def _persist_thinking(dsn: str | None, session_id: str, segments: list[str]) -> None:
    """Persist captured thinking segments as ``role='thinking'`` chat turns carrying
    the host model pin, so a later thinking-view can render REAL reasoning."""
    store = _chat_store(dsn)
    for seg in segments:
        store.append_turn(session_id, "thinking", seg, HOST_AGUI_MODEL)


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #


@studio_agent.tool
async def revise_plan(
    ctx: RunContext[StudioDeps],
    goal: str | None = None,
    audience: str | None = None,
    channels: list[str] | None = None,
    sections: list[str] | None = None,
    schedule: dict[str, str] | None = None,
) -> Any:
    """Apply the operator's edits to the SHARED campaign plan, persist it, and
    snap the new state back to the UI. Pass ONLY the fields that changed."""
    plan = ctx.deps.state
    if goal is not None:
        plan.goal = goal
    if audience is not None:
        plan.audience = audience
    if channels is not None:
        plan.channels = channels
    if sections is not None:
        plan.sections = sections
    if schedule is not None:
        plan.schedule = schedule

    await asyncio.to_thread(_persist_plan, ctx.deps.dsn, ctx.deps.session_id, plan)
    await asyncio.to_thread(
        _log_turn,
        ctx.deps.dsn,
        ctx.deps.session_id,
        "host",
        f"[plan] revised: goal={plan.goal!r} audience={plan.audience!r} "
        f"channels={plan.channels}",
        HOST_AGUI_MODEL,
    )

    # Emit an AG-UI STATE_SNAPSHOT so the frontend's shared state updates. Imported
    # lazily so importing this module never requires the ag-ui protocol package.
    from ag_ui.core import EventType, StateSnapshotEvent

    return StateSnapshotEvent(
        type=EventType.STATE_SNAPSHOT, snapshot=plan.model_dump()
    )


@studio_agent.tool
async def describe_brand_voice(ctx: RunContext[StudioDeps]) -> str:
    """Return the studio's REAL, currently-loaded brand voice so you can tell the
    operator EXACTLY what voice you write in — tone, structure, preferred + banned
    lexicon, and the approved-claims allow-list — resolved from the tenant pack
    (the same source the copywriter + critic cells write/judge in). Call this whenever
    the operator asks what brand voice you are using; never claim you don't know it.
    Honest: if the pack genuinely cannot resolve, say so and name the tenant."""

    def _resolve() -> tuple[str, tuple[str, ...], str]:
        from studio.customer_research import _DEFAULT_TENANT, resolve_brand_voice

        # Prefer the run's tenant; fall back to the configured default tenant so the
        # real voice surfaces even when deps carry a placeholder tenant.
        tid = ctx.deps.tenant_id
        voice, claims = resolve_brand_voice(tid)
        if not voice.strip():
            tid = _DEFAULT_TENANT
            voice, claims = resolve_brand_voice(tid)
        return voice, claims, tid

    voice, claims, tid = await asyncio.to_thread(_resolve)
    if not voice.strip():
        return (
            f"BRAND VOICE: the brand-voice pack for tenant '{tid}' could not be "
            "resolved right now, so I cannot quote the exact dimensions. It is "
            "configured per pack and the copywriter cell loads it at draft time — "
            "I do have a brand voice; I just can't render its text this moment."
        )
    parts = [
        f"Brand voice in use (tenant '{tid}'), resolved from the tenant pack — this "
        "is what the copywriter writes in and the critic judges against:",
        "",
        voice.strip(),
    ]
    if claims:
        parts += [
            "",
            "Approved claims (the ONLY factual/credential/offer claims I may make; "
            "anything else is blocked as off-voice):",
            *(f"- {c}" for c in claims),
        ]
    return "\n".join(parts)


@studio_agent.tool
async def brainstorm_with_roles(ctx: RunContext[StudioDeps]) -> str:
    """Run the REAL role cells over the current plan: funnel-architect → copywriter
    → independent critic → Opus jury. Each contribution is logged to
    ``studio_chat_turns`` as a LABELED, model-pinned turn. Returns a short summary."""
    from cells.copywriter import build_copywriter_cell
    from cells.critic import CRITIC_MODEL, build_critic_cell
    from cells.funnel_architect import FUNNEL_MODEL, build_funnel_architect_cell

    plan = ctx.deps.state
    dsn, sid = ctx.deps.dsn, ctx.deps.session_id
    brief = (
        f"Objective: {plan.goal or 'grow bookings'}\n"
        f"Audience: {plan.audience or 'local clients seeking custom tattoos'}\n"
        f"Channels: {', '.join(plan.channels) or 'instagram, email'}"
    )

    # 1) Funnel architect (real cell, Sonnet pin)
    funnel = await build_funnel_architect_cell().run(brief)
    plan.assets = [a.model_dump() for a in funnel.assets]
    await asyncio.to_thread(_persist_plan, dsn, sid, plan)
    await asyncio.to_thread(
        _log_turn, dsn, sid, "funnel_architect",
        f"[funnel_architect] {funnel.primary_conversion} | "
        + "; ".join(f"{a.stage.value}:{a.asset_type}" for a in funnel.assets),
        FUNNEL_MODEL if isinstance(FUNNEL_MODEL, str) else str(FUNNEL_MODEL),
    )

    # 2) Copywriter (real cell, Sonnet pin) — execute the first planned asset
    first = funnel.assets[0]
    copy = await build_copywriter_cell().run(
        f"Winning angle: {first.purpose}\nPlatform: instagram\nGoal: {plan.goal}"
    )
    top = copy.variants[0]
    await asyncio.to_thread(
        _log_turn, dsn, sid, "copywriter",
        f"[copywriter] hook: {top.hook} | CTA: {top.call_to_action}",
        "anthropic:claude-sonnet-4-6",
    )

    # 3) Critic — a real INDEPENDENT pass over the copy (never a staged debate)
    critique = await build_critic_cell().run(
        f"Asset to critique (instagram caption):\nHook: {top.hook}\n"
        f"Caption: {top.caption}\nCTA: {top.call_to_action}"
    )
    await asyncio.to_thread(
        _log_turn, dsn, sid, "critic",
        f"[critic] verdict={critique.verdict.value} — {critique.rationale[:160]}",
        CRITIC_MODEL if isinstance(CRITIC_MODEL, str) else str(CRITIC_MODEL),
    )

    # 4) Jury (Opus) — a final go/no-go over the brainstorm
    jury = Agent(
        JURY_MODEL,
        instructions=(
            "You are the Opus jury for a marketing studio. Given the funnel, the "
            "copy, and the critic's verdict, give a 1-2 sentence final go/no-go and "
            "the single most important fix. Be decisive and honest."
        ),
        model_settings={"temperature": 0.0},
        defer_model_check=True,
    )
    verdict = await jury.run(
        f"Funnel conversion: {funnel.primary_conversion}\n"
        f"Copy hook: {top.hook}\nCritic verdict: {critique.verdict.value} "
        f"({critique.rationale[:200]})"
    )
    await asyncio.to_thread(
        _log_turn, dsn, sid, "jury", f"[jury] {verdict.output}", JURY_MODEL
    )

    return (
        f"Brainstorm complete: {len(funnel.assets)} planned assets, "
        f"critic verdict={critique.verdict.value}. Jury: {verdict.output[:200]}"
    )


# --------------------------------------------------------------------------- #
# Shared traced-run logic — used by BOTH the Haiku-triggered ``run_campaign``
# tool AND the deterministic ``POST /studio/run`` button path, so they can never
# diverge. All sync (offloaded once via ``asyncio.to_thread`` by each caller).
# --------------------------------------------------------------------------- #


def _brief_from_plan(plan: CampaignPlan) -> str:
    brief = (
        f"Goal: {plan.goal or 'grow bookings'}\n"
        f"Audience: {plan.audience or 'local clients seeking custom tattoos'}\n"
        f"Channels: {', '.join(plan.channels) or 'instagram, email'}"
        + (f"\nSections: {', '.join(plan.sections)}" if plan.sections else "")
    )
    # Carry the interview-gathered framing into the run brief so the deterministic
    # spine's cells (strategist/copywriter/critic) ground in exactly what the operator
    # agreed to — not just the conversational host. Each line is only added when set.
    if plan.campaign_type.strip():
        brief += f"\nCampaign type: {plan.campaign_type.strip()}"
    if plan.action_type.strip():
        brief += f"\nAction: {plan.action_type.strip()}"
    if plan.tone.strip():
        brief += f"\nTone / brand voice: {plan.tone.strip()}"
    if plan.output_count and plan.output_count > 0:
        brief += f"\nDrafts requested: {plan.output_count}"
    # Carry a REAL summary of the uploaded customer list so the downstream draft agents
    # know who they're writing for (size + columns). The full per-lead rows are handled
    # by the grounded research_and_stage_leads path; this is the spine-level summary.
    cust = plan.customers or {}
    if cust.get("rows"):
        cols = ", ".join(str(x) for x in (cust.get("columns") or []))
        brief += f"\nUploaded customer list: {cust['rows']} row(s)" + (f"; columns: {cols}" if cols else "")
    # Carry uploaded brand / strategy notes into the run brief too. Bounded so a large
    # notes file can't blow the brief out.
    notes = plan.notes.strip()
    if notes:
        if len(notes) > 2000:
            notes = notes[:2000] + " …[truncated]"
        brief += f"\nBrand / strategy notes (operator-provided): {notes}"
    return brief


def _summary_text(summary: dict[str, Any]) -> str:
    chans = ", ".join(summary.get("channels", [])) or "the selected channels"
    runs_note = (
        " You can watch each agent's step in the Runs tab."
        if summary.get("runs_row")
        else " (Per-agent traces are in this thread; the Runs-tab row was unavailable.)"
    )
    return (
        f"Ran the '{summary.get('archetype_id')}' campaign (run {summary.get('run_id')}). "
        f"The team produced {summary.get('n_queued', 0)} draft(s) across {chans} and staged "
        f"{summary.get('n_pending', 0)} action(s) PENDING approval — everything is HELD, "
        f"nothing was sent.{runs_note} Want me to refine a draft or stage one for approval?"
    )


def _use_provided_leads(plan: CampaignPlan) -> bool:
    """The hard compliance branch: True iff the operator chose to use ONLY their own
    leads (uploaded CSV / existing DB) rather than sourcing new ones from the web."""
    from studio.interview import LEAD_SOURCE_PROVIDED

    return (plan.lead_source or "").strip().lower() == LEAD_SOURCE_PROVIDED


def _execute_campaign_sync(
    plan: CampaignPlan, session_id: str, tenant_id: str, dsn: str | None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """SYNC: run the real traced Phase-A campaign for ``plan`` and persist its visible
    surfaces — mirror each per-role trace into the thread as a LABELED turn (so the
    operator can watch what each agent thought) and reflect the work into the shared
    plan. ``run_id`` lets the async endpoint poll the per-role ``agent_runs`` live.
    Returns the runner summary. NOTHING is sent (HELD/PENDING only).

    Branches on the operator's LEAD-SOURCE choice: ``provided`` runs the per-lead
    compliance path (target ONLY the operator's leads, research each one); otherwise
    the web-sourcing Phase-A spine runs."""
    if _use_provided_leads(plan):
        return _execute_provided_leads_sync(plan, session_id, tenant_id, dsn, run_id)

    from studio.campaign_runner import run_and_trace

    summary = run_and_trace(
        brief=_brief_from_plan(plan), tenant_id=tenant_id, dsn=dsn, run_id=run_id,
        force_research=bool(plan.deep_research),
        output_count=plan.output_count or 0,
        campaign_type=plan.campaign_type or None,
    )
    for ar in summary.get("agent_runs", []):
        role = str(ar.get("role") or "host")
        role = role if role in VALID_ROLES else "host"
        _log_turn(dsn, session_id, role, f"[{role}] {ar.get('output_summary', '')}", ar.get("model"))
    if summary.get("agent_runs"):
        plan.tasks_per_role = {
            str(ar.get("role")): [str(ar.get("output_summary", ""))[:160]]
            for ar in summary["agent_runs"]
        }
        _persist_plan(dsn, session_id, plan)
    # Assemble + persist the per-campaign spec doc from the REAL plan + the real
    # per-role agent_runs + the selected archetype spec. Best-effort: never break a
    # real run if the spec store or registry is unavailable. NOTHING here sends.
    _persist_campaign_spec(plan, summary, session_id, tenant_id, dsn)
    return summary


def _execute_provided_leads_sync(
    plan: CampaignPlan, session_id: str, tenant_id: str, dsn: str | None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """SYNC: the LEAD-SOURCE=provided compliance run. Targets ONLY the operator's own
    leads — the uploaded CSV (its ingested ``customer_ids``) or, if none uploaded, the
    existing-DB churn-risk cohort — and for EACH lead:

      * pulls its REAL grounded facts (DB history: persona, tattoo history, city,
        prior-campaign memories) via ``lookup_leads`` — never a substituted/random lead,
      * runs deep research ABOUT that specific lead's studio (Firecrawl, cited, real
        URLs only) when deep research is on, recorded on a per-lead ``researcher`` step,
      * writes a personalized draft in the studio's OWN brand voice
        (``build_outreach_draft`` -> ``resolve_brand_voice`` + the copywriter cell),
        grounded ONLY in that lead's real facts/sources (no fabrication),
      * stages the draft as a PENDING ``actions`` row keyed to this run (HELD), and
      * records a memory of the outreach.

    Records per-lead ``researcher`` + ``draft`` ``agent_runs`` plus a final ``jury``
    summary and materializes a ``runs`` row, so the agency war-room renders it like any
    run and each staged draft deep-links to its exact Review-Queue row. NOTHING sends."""
    import uuid as _uuid

    from actions.store import ensure_schema, record_pending_action
    from memory import MemoryStore
    from studio.campaign_runner import _materialize_runs_row, _summarize_output
    from studio.customer_research import (
        _research_enabled,
        build_outreach_draft,
        churn_risk_leads,
        lookup_leads,
        research_studio,
    )

    if not run_id:
        campaign_id = f"camp_{_uuid.uuid4().hex[:12]}"
        run_id = f"team-{campaign_id}-{_uuid.uuid4().hex[:12]}"
    else:
        parts = run_id.split("-")
        campaign_id = parts[1] if len(parts) >= 2 and parts[1].startswith("camp_") else f"camp_{_uuid.uuid4().hex[:12]}"

    store = MemoryStore(dsn=dsn)
    store.ensure_schema()
    ensure_schema(dsn)
    from team.store import TeamStore

    ts = TeamStore(dsn)
    ts.setup()

    # 1) Resolve ONLY the operator's leads — uploaded CSV ids first, else DB cohort.
    cust_ids = list((plan.customers or {}).get("customer_ids") or [])
    if cust_ids:
        leads = lookup_leads(
            tenant_id, [{"customer_id": i} for i in cust_ids], dsn=dsn, memory_store=store
        )
        source_note = f"uploaded CSV ({len(leads)} of {len(cust_ids)} rows resolved in DB)"
    else:
        limit = plan.lead_count or plan.output_count or 10
        leads = churn_risk_leads(tenant_id, limit=limit, dsn=dsn, memory_store=store)
        source_note = f"existing-database win-back / lapsing cohort ({len(leads)})"

    goal = plan.goal or "win back lapsed clients"
    deep = _research_enabled(plan.deep_research)
    agent_runs: list[dict[str, Any]] = []
    pending: list[str] = []

    def _rec(role: str, model: str, inp: dict[str, Any], out: dict[str, Any]) -> None:
        ts.record_agent_run(
            id=f"ar_{_uuid.uuid4().hex[:16]}", campaign_id=campaign_id, run_id=run_id,
            role=role, model=model, input=inp, output=out,
        )
        agent_runs.append({
            "role": role, "model": model, "input": inp, "output": out,
            "output_summary": _summarize_output(role, out),
        })

    # 2) Per-lead: real DB history + research ABOUT this lead + brand-voiced draft.
    for facts in leads:
        cust_id = facts["customer_id"]
        research = research_studio(facts, enabled=deep)  # real Firecrawl about THIS studio
        th = facts.get("tattoo_history", []) or []
        traits = facts.get("persona_traits", {}) or {}
        sources = [
            {"url": r.get("url"), "title": r.get("title"), "snippet": r.get("snippet")}
            for r in research if r.get("url")
        ][:5]
        _rec(
            "researcher", "firecrawl+customer_db",
            {"customer_id": cust_id, "name": facts.get("name")},
            {
                "cited": len(sources), "sources": sources,
                "lead": facts.get("name"), "customer_id": cust_id,
                "db_history": {
                    "city": facts.get("city"), "past_tattoos": len(th),
                    "interests": facts.get("interests", []),
                    "lifecycle": traits.get("lifecycle_stage"),
                    "win_back_candidate": traits.get("win_back_candidate"),
                    "prior_memories": len(facts.get("memories", []) or []),
                },
                "degraded": deep and len(sources) == 0,
            },
        )

        draft = build_outreach_draft(
            facts, goal=goal, plan_channels=plan.channels or None,
            deep_research=plan.deep_research, research=research,
        )
        copy_model = (
            "anthropic:claude-sonnet-4-6"
            if any(g == "copy=copywriter_email_cell" for g in draft.get("grounding", []))
            else "grounded_template"
        )
        _rec(
            "draft", copy_model,
            {"customer_id": cust_id, "channel": draft["channel"]},
            {
                "hook": draft.get("subject") or "", "headline": draft.get("subject") or "",
                "caption": draft.get("draft") or "", "channel": draft["channel"],
                "grounding": draft.get("grounding", []),
            },
        )

        action_id = record_pending_action(
            tenant_id=tenant_id, decision_id=None, type="outreach",
            channel=draft["channel"], worker="studio_provided_leads",
            target=draft["target"], draft=draft["draft"], subject=draft.get("subject"),
            conf=None, threshold=None, esc_kind="approval_required",
            esc_label="Provided-lead outreach — operator approval required",
            idempotency_key=f"{run_id}:{cust_id}", run_id=run_id, dsn=dsn,
        )
        pending.append(action_id)
        try:
            store.write(
                tenant_id=tenant_id, subject_type="customer", subject_id=cust_id,
                text=(
                    f"Staged {draft['channel']} outreach to {facts.get('name')} for goal "
                    f"'{goal}'. Grounded on: {', '.join(draft.get('grounding', []))}."
                ),
                metadata={"kind": "outreach", "session_id": session_id,
                          "action_id": action_id, "run_id": run_id},
            )
        except Exception:
            pass

    # 3) A final jury summary over the per-lead drafts (offline aggregate, HELD).
    _rec(
        "jury", JURY_MODEL,
        {"n_leads": len(leads), "lead_source": "provided"},
        {
            "aggregate": 1.0 if pending else 0.0, "decision": "review",
            "note": (f"{len(pending)} per-lead draft(s) staged HELD from {source_note}; "
                     "approve-first — nothing sent"),
        },
    )

    runs_row = _materialize_runs_row(
        dsn=dsn, run_id=run_id, tenant_id=tenant_id, agent_runs=agent_runs
    )

    # Mirror each per-role trace into the chat thread (same as the spine path).
    for ar in agent_runs:
        role = str(ar.get("role") or "host")
        role = role if role in VALID_ROLES else "host"
        _log_turn(dsn, session_id, role, f"[{role}] {ar.get('output_summary', '')}", ar.get("model"))

    channels = sorted({str(ar["input"].get("channel")) for ar in agent_runs if ar["role"] == "draft" and ar["input"].get("channel")})
    plan.tasks_per_role = {
        "researcher": [f"researched {len(leads)} provided lead(s) from {source_note}"],
        "draft": [f"{len(pending)} per-lead brand-voiced draft(s) staged HELD"],
    }
    try:
        _persist_plan(dsn, session_id, plan)
    except Exception:
        pass

    return {
        "run_id": run_id,
        "campaign_id": campaign_id,
        "archetype_id": "provided_leads",
        "lead_source": "provided",
        "source_note": source_note,
        "agent_runs": agent_runs,
        "n_pending": len(pending),
        "n_queued": len(pending),
        "channels": channels,
        "step_notes": [
            f"lead_source=provided: targeting ONLY the operator's leads — {source_note}",
            f"researched {len(leads)} lead(s) per-lead (DB history + cited web research about each)",
            f"staged {len(pending)} brand-voiced draft(s) HELD (approve-first); nothing sent",
        ],
        "runs_row": runs_row,
    }


def _persist_campaign_spec(
    plan: CampaignPlan, summary: dict[str, Any], session_id: str,
    tenant_id: str, dsn: str | None,
) -> None:
    """Assemble the per-campaign spec from ALREADY-REAL fields and upsert it.

    ``plan`` provides goal/audience/channels/sections/schedule; ``summary`` provides
    run_id/campaign_id/archetype_id + the per-role agent_runs; the archetype
    registry provides success_metric/trigger/steps_enabled. Wrapped best-effort so a
    spec-store/registry hiccup can never fail an otherwise-successful run."""
    try:
        from studio import campaign_spec_store as spec_store

        run_id = summary.get("run_id")
        if not run_id:
            return

        archetype_id = summary.get("archetype_id")
        archetype_meta: dict[str, Any] | None = None
        if archetype_id:
            try:
                from archetypes import registry

                aspec = registry.get(archetype_id)
                trig = getattr(aspec.trigger, "value", aspec.trigger)
                archetype_meta = {
                    "success_metric": aspec.success_metric,
                    "trigger": trig,
                    "steps_enabled": sorted(
                        getattr(s, "value", s) for s in aspec.steps_enabled
                    ),
                }
            except Exception:
                archetype_meta = None

        content, markdown = spec_store.assemble_spec(
            run_id=str(run_id),
            campaign_id=summary.get("campaign_id"),
            tenant_id=tenant_id,
            session_id=session_id,
            archetype_id=archetype_id,
            plan={
                "goal": plan.goal,
                "audience": plan.audience,
                "channels": list(plan.channels or []),
                "sections": list(plan.sections or []),
                "schedule": dict(plan.schedule or {}),
            },
            agent_runs=summary.get("agent_runs") or [],
            n_pending=summary.get("n_pending"),
            n_queued=summary.get("n_queued"),
            channels=summary.get("channels") or [],
            step_notes=summary.get("step_notes") or [],
            archetype_meta=archetype_meta,
        )
        spec_store.upsert_spec(
            str(run_id),
            campaign_id=summary.get("campaign_id"),
            tenant_id=tenant_id,
            session_id=session_id,
            archetype_id=archetype_id,
            content=content,
            markdown=markdown,
            dsn=dsn,
        )
    except Exception:
        # Spec doc is a read-surface convenience; never let it break a real run.
        pass


async def launch_studio_run(
    app,
    dsn: str | None,
    session_id: str,
    tenant_id: str,
    plan: CampaignPlan,
    *,
    trigger_note: str = "Run the campaign now.",
) -> dict[str, Any]:
    """Start ONE deterministic held campaign run in the background and return its
    identifiers immediately. This is the single shared launch path behind BOTH the
    ``POST /studio/run`` button and the voice ``request_orchestration`` GO-gate, so
    they can never diverge: same registry, same traced Phase-A spine, same HELD
    posture (writes ``agent_runs`` + PENDING ``actions``; NOTHING is sent).

    The caller (an async route) owns the event loop; we register the run on
    ``app.state._studio_runs`` (the same dict the ``GET /studio/run/{id}`` poller
    reads) and schedule the real work as a background task."""
    if not hasattr(app.state, "_studio_runs"):
        app.state._studio_runs = {}
    runs_registry: dict[str, dict] = app.state._studio_runs

    campaign_id = f"camp_{uuid.uuid4().hex[:12]}"
    run_id = f"team-{campaign_id}-{uuid.uuid4().hex[:12]}"
    runs_registry[run_id] = {"status": "running", "summary": None, "error": None}

    try:
        await asyncio.to_thread(_log_turn, dsn, session_id, "operator", trigger_note, None)
    except Exception:
        pass

    async def _bg() -> None:
        try:
            summary = await asyncio.to_thread(
                _execute_campaign_sync, plan, session_id, tenant_id, dsn, run_id
            )
            runs_registry[run_id] = {"status": "completed", "summary": summary, "error": None}
            try:
                await asyncio.to_thread(
                    _log_turn, dsn, session_id, "host", _summary_text(summary), HOST_AGUI_MODEL
                )
            except Exception:
                pass
        except Exception as exc:  # honest failure, never a fake success
            runs_registry[run_id] = {
                "status": "error", "summary": None, "error": f"{type(exc).__name__}: {exc}"
            }

    asyncio.create_task(_bg())
    return {"runId": run_id, "campaignId": campaign_id, "status": "running"}

@studio_agent.tool
async def run_campaign(ctx: RunContext[StudioDeps]) -> str:
    """Run the REAL, traced campaign for the CURRENT plan. Call when the operator asks
    to RUN / launch / execute / kick off the campaign (or approves the plan to run).

    Classifies the plan to a registered archetype, then runs the WIRED Phase-A spine
    (research -> strategy -> draft x N (capped) -> independent critique -> route pinned
    to HOLD -> queue). Writes per-role ``agent_runs`` + queued ``assets`` + PENDING
    ``actions`` and materializes a ``runs`` row whose steps are the per-agent traces
    (watchable node-by-node in the Runs tab). NOTHING IS SENT — every output is
    HELD/PENDING behind approve-first. Returns a short honest summary."""
    summary = await asyncio.to_thread(
        _execute_campaign_sync,
        ctx.deps.state,
        ctx.deps.session_id,
        ctx.deps.tenant_id,
        ctx.deps.dsn,
    )
    return _summary_text(summary)


@studio_agent.tool(requires_approval=True)
async def stage_publish(
    ctx: RunContext[StudioDeps], channel: str, draft: str, target: str | None = None
) -> str:
    """Stage a would-SEND action behind the HOLD gate. ``requires_approval=True`` so
    pydantic-ai surfaces this as an UNAPPROVED deferred request — it never
    auto-fires. Even after approval this only writes a PENDING ``actions`` row; the
    real send stays held on the existing approve-first path. NOTHING is sent here."""
    from actions.store import ensure_schema, record_pending_action

    dsn = ctx.deps.dsn or os.environ.get("ENGINE_DATABASE_URL")
    await asyncio.to_thread(ensure_schema, dsn)
    action_id = await asyncio.to_thread(
        lambda: record_pending_action(
            tenant_id=ctx.deps.tenant_id,
            decision_id=None,
            type="post",
            channel=channel,
            worker="studio_agui",
            target=target,
            draft=draft,
            conf=None,
            threshold=None,
            esc_kind="approval_required",
            esc_label="Studio publish — operator approval required",
            idempotency_key=f"studio:{ctx.deps.session_id}:{uuid.uuid4().hex[:12]}",
            dsn=dsn,
        )
    )
    return (
        f"STAGED (held): action {action_id} on {channel} is PENDING approval. "
        "Nothing has been sent."
    )


# --------------------------------------------------------------------------- #
# Customer research + per-lead grounded drafting (the grounding + memory layer)
# --------------------------------------------------------------------------- #


def _format_lead_facts(facts: dict[str, Any]) -> str:
    """Render grounded lead facts as a compact, honest block for the Host to reason
    over. Only fields the DB actually returned appear."""
    traits = facts.get("persona_traits", {})
    parts = [f"{facts.get('name')} ({facts.get('customer_id')})"]
    if facts.get("city"):
        parts.append(f"{facts['city']}, {facts.get('state') or ''}".strip(", "))
    if facts.get("interests"):
        parts.append("interests=" + ", ".join(facts["interests"]))
    for key in ("aesthetic_lean", "lifecycle_stage", "win_back_candidate", "likely_best_channel"):
        if key in traits:
            parts.append(f"{key}={traits[key]}")
    th = facts.get("tattoo_history", [])
    parts.append(f"past_tattoos={len(th)}" + (f" (last: {th[0]['style']})" if th else ""))
    if facts.get("memories"):
        parts.append(f"prior_memories={len(facts['memories'])}")
    return " | ".join(str(p) for p in parts)


@studio_agent.tool
async def research_lead(
    ctx: RunContext[StudioDeps],
    email: str | None = None,
    name: str | None = None,
    customer_id: str | None = None,
) -> str:
    """Pull ONE lead's REAL grounded facts from the customer DB + memory layer.

    Resolves by email, name, or customer_id (tenant-scoped). Returns interests, past
    tattoos, city, persona psychology, and any prior-campaign memories so you can
    reason per-lead. Returns an honest 'not found' if the lead is not in the DB —
    never invent facts."""
    from studio.customer_research import lookup_lead

    def _work() -> dict[str, Any] | None:
        from memory import MemoryStore

        return lookup_lead(
            ctx.deps.tenant_id,
            email=email,
            name=name,
            customer_id=customer_id,
            dsn=ctx.deps.dsn,
            memory_store=MemoryStore(dsn=ctx.deps.dsn),
        )

    facts = await asyncio.to_thread(_work)
    if facts is None:
        ident = email or name or customer_id
        return f"No customer found for {ident!r} in tenant {ctx.deps.tenant_id}. (Honest: this lead is not in the DB.)"
    return "Grounded facts: " + _format_lead_facts(facts)


def _research_and_stage_sync(
    plan: CampaignPlan,
    session_id: str,
    tenant_id: str,
    dsn: str | None,
    *,
    emails: list[str] | None,
    limit: int,
) -> dict[str, Any]:
    """SYNC: research a batch of leads one-by-one, build a PERSONALIZED grounded draft
    per lead, persist each as a PENDING ``actions`` row (HELD), and WRITE a per-lead +
    a campaign memory so the Host remembers this outreach next run. NOTHING is sent."""
    from actions.store import ensure_schema, record_pending_action

    from memory import MemoryStore
    from studio.customer_research import (
        build_outreach_draft,
        churn_risk_leads,
        lookup_leads,
    )

    store = MemoryStore(dsn=dsn)
    store.ensure_schema()
    ensure_schema(dsn)

    if emails:
        leads = lookup_leads(
            tenant_id, [{"email": e} for e in emails], dsn=dsn, memory_store=store
        )
        requested = len(emails)
    else:
        leads = churn_risk_leads(tenant_id, limit=limit, dsn=dsn, memory_store=store)
        requested = len(leads)

    goal = plan.goal or "win back lapsed clients"
    staged: list[dict[str, Any]] = []
    for facts in leads:
        draft = build_outreach_draft(
            facts, goal=goal, plan_channels=plan.channels or None
        )
        cust_id = facts["customer_id"]
        action_id = record_pending_action(
            tenant_id=tenant_id,
            decision_id=None,
            type="outreach",
            channel=draft["channel"],
            worker="studio_agui_research",
            target=draft["target"],
            draft=draft["draft"],
            subject=draft["subject"],
            conf=None,
            threshold=None,
            esc_kind="approval_required",
            esc_label="Studio per-lead outreach — operator approval required",
            idempotency_key=f"studio:{session_id}:{cust_id}:outreach",
            dsn=dsn,
        )
        # Persistent memory of this outreach (per customer) — internal context only.
        store.write(
            tenant_id=tenant_id,
            subject_type="customer",
            subject_id=cust_id,
            text=(
                f"Staged {draft['channel']} outreach to {facts.get('name')} for goal "
                f"'{goal}'. Grounded on: {', '.join(draft['grounding'])}. "
                f"Draft opener: {draft['draft'][:100]}"
            ),
            metadata={
                "kind": "outreach",
                "session_id": session_id,
                "action_id": action_id,
                "channel": draft["channel"],
            },
        )
        staged.append(
            {
                "customer_id": cust_id,
                "name": facts.get("name"),
                "channel": draft["channel"],
                "action_id": action_id,
                "target": draft["target"],
            }
        )

    channels = sorted({s["channel"] for s in staged})
    # Campaign-level memory (cross-run learning) keyed to this session.
    if staged:
        store.write(
            tenant_id=tenant_id,
            subject_type="campaign",
            subject_id=session_id,
            text=(
                f"Researched {len(staged)} leads and staged {len(staged)} personalized "
                f"PENDING outreach drafts for goal '{goal}' across {', '.join(channels)}. "
                f"Leads: {', '.join(s['name'] or s['customer_id'] for s in staged[:10])}."
            ),
            metadata={"kind": "campaign_summary", "session_id": session_id, "n": len(staged)},
        )
    return {
        "requested": requested,
        "n_leads": len(leads),
        "n_drafts": len(staged),
        "channels": channels,
        "staged": staged,
        "not_found": max(0, requested - len(leads)) if emails else 0,
    }


@studio_agent.tool
async def research_and_stage_leads(
    ctx: RunContext[StudioDeps],
    emails: list[str] | None = None,
    limit: int = 10,
) -> str:
    """Research a BATCH of leads one-by-one and stage a PERSONALIZED outreach draft
    PER lead in the Review Queue (PENDING / HELD — nothing sent).

    Pass ``emails`` to target specific uploaded leads; omit it to target the tenant's
    churn-risk / lapsing cohort (capped by ``limit``). For each lead this pulls real
    grounded facts, builds a personalized draft from those facts (no invented
    details), writes a PENDING ``actions`` row, and records a memory of the outreach
    so you remember it next time. Returns an honest summary of what was staged."""
    summary = await asyncio.to_thread(
        _research_and_stage_sync,
        ctx.deps.state,
        ctx.deps.session_id,
        ctx.deps.tenant_id,
        ctx.deps.dsn,
        emails=emails,
        limit=limit,
    )
    lead_lines = "; ".join(
        f"{s['name']}→{s['channel']} (action {s['action_id']})" for s in summary["staged"][:10]
    )
    nf = summary.get("not_found", 0)
    nf_note = f" {nf} requested lead(s) were not in the DB (honest miss)." if nf else ""
    return (
        f"Researched {summary['n_leads']} lead(s) and staged {summary['n_drafts']} "
        f"personalized PENDING outreach draft(s) across {', '.join(summary['channels']) or 'n/a'} "
        f"— all HELD in the Review Queue, nothing sent.{nf_note} {lead_lines}"
    )


# --------------------------------------------------------------------------- #
# Customer CSV upload. This helper is the PURE PARSER only (no side effects). The
# `POST /studio/upload` route below is what then INGESTS the rows into `customers`
# AND attaches a real summary onto the session plan, which `_customers_context`
# injects into the supervisor on every turn (so it genuinely reads the CSV) — see
# that route + `_customers_context` above. Do not read "(parser)" as "upload never
# ingests": it does.
# --------------------------------------------------------------------------- #


def parse_customers_csv(content: str, filename: str = "upload.csv") -> dict[str, Any]:
    """Parse an uploaded customers CSV and return an honest preview (PURE parse — the
    ``/studio/upload`` route does the ingestion + plan-attach; this fn has no I/O).

    This is a REAL parse (``csv`` over the actual bytes the operator uploaded) — it
    does NOT ingest anything into the customers table; full ingestion is a separate,
    later step. Returns the data-row count, the header columns, and a small sample
    of the first rows so the UI can acknowledge exactly what was parsed.

    Raises ``ValueError`` on empty/unparseable input so the caller returns a 400.
    """
    import csv as _csv
    import io as _io

    text = (content or "").lstrip("﻿")  # drop a leading UTF-8 BOM if present
    if not text.strip():
        raise ValueError("empty file — no CSV content")

    reader = _csv.reader(_io.StringIO(text))
    rows = [r for r in reader if any((c or "").strip() for c in r)]
    if not rows:
        raise ValueError("no rows parsed")

    header = [c.strip() for c in rows[0]]
    data = rows[1:]

    def _row_to_obj(r: list[str]) -> dict[str, str]:
        obj: dict[str, str] = {}
        for i, cell in enumerate(r):
            key = header[i] if i < len(header) else f"col{i + 1}"
            obj[key] = cell if cell is not None else ""
        return obj

    return {
        "ok": True,
        "filename": filename,
        "rows": len(data),
        "columns": header,
        "sample": [_row_to_obj(r) for r in data[:5]],
        "ingested": False,  # honesty: parsed only, not written to the customers table
    }


# --------------------------------------------------------------------------- #
# FastAPI mount
# --------------------------------------------------------------------------- #


def _load_plan(session_id: str, dsn: str | None) -> CampaignPlan:
    """Load this session's most recent persisted plan, if any, so edits accumulate."""
    try:
        rows = latest_plans(1, session_id=session_id, dsn=dsn)
    except Exception:
        return CampaignPlan()
    if rows:
        try:
            return CampaignPlan.model_validate(rows[0]["state"])
        except Exception:
            return CampaignPlan()
    return CampaignPlan()


def mount_studio_agui(app) -> None:
    """Mount ``POST /studio/agui`` alongside the existing /graphql + SSE."""
    from pydantic_ai.ui.ag_ui import AGUIAdapter

    from obsapi.db import get_dsn

    if getattr(app.state, "_studio_agui_mounted", False):
        return
    app.state._studio_agui_mounted = True

    @app.post("/studio/agui")
    async def studio_agui_route(request: Request):  # noqa: ANN202
        dsn = get_dsn()
        body = await request.body()  # cached by starlette; the adapter re-reads it
        try:
            payload = json.loads(body or b"{}")
        except Exception:
            payload = {}
        session_id = (
            payload.get("threadId")
            or payload.get("thread_id")
            or request.query_params.get("session_id")
            or "studio-default"
        )

        # Persist the operator's latest message as an 'operator' turn.
        last_user = ""
        for msg in payload.get("messages", []) or []:
            if msg.get("role") == "user":
                content = msg.get("content")
                last_user = content if isinstance(content, str) else str(content)
        if last_user:
            try:
                await asyncio.to_thread(
                    _log_turn, dsn, session_id, "operator", last_user, None
                )
            except Exception:
                pass

        # Tenant the studio writes under (PENDING actions, materialized runs, assets).
        # Env-overridable so the booter can ALIGN it with the console's
        # NEXT_PUBLIC_TENANT_ID — otherwise studio output lands in a tenant the
        # Runs/Review tabs don't query. Default "demo" matches the existing data.
        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")
        deps = StudioDeps(
            state=_load_plan(session_id, dsn),
            session_id=session_id,
            tenant_id=tenant_id,
            dsn=dsn,
        )

        async def on_complete(result) -> Any:
            # Persist the host's final reply (Haiku) and the final plan state.
            out = getattr(result, "output", None)
            text = out if isinstance(out, str) else None
            try:
                # Capture + persist the host's REAL extended-thinking trace first, so
                # the thinking-view can show the reasoning behind this reply.
                segments = _extract_thinking(result)
                if segments:
                    await asyncio.to_thread(
                        _persist_thinking, dsn, session_id, segments
                    )
                if text:
                    await asyncio.to_thread(
                        _log_turn, dsn, session_id, "host", text, HOST_AGUI_MODEL
                    )
                await asyncio.to_thread(_persist_plan, dsn, session_id, deps.state)
            except Exception:
                pass
            return
            yield  # make this an async generator (OnCompleteFunc may yield events)

        return await AGUIAdapter.dispatch_request(
            request, agent=studio_agent, deps=deps, on_complete=on_complete
        )

    # In-process registry of async studio runs (status + final summary). Lost on
    # restart — fine, these runs complete in ~60s. The live FE polls
    # GET /studio/run/{id}, which reads agent_runs (written incrementally by the
    # spine) PLUS this status, so the operator watches each agent land in real time.
    if not hasattr(app.state, "_studio_runs"):
        app.state._studio_runs = {}
    runs_registry: dict[str, dict] = app.state._studio_runs

    @app.post("/studio/run")
    async def studio_run_start(request: Request):  # noqa: ANN202
        """DETERMINISTIC async campaign run — the 'Run campaign' BUTTON path, made LIVE.

        Bypasses the Haiku host's free-text decision entirely: loads the session's
        persisted plan (optionally merged with an inline override the button sends so
        the run matches exactly what the operator sees), then returns the run_id
        IMMEDIATELY and runs the real traced Phase-A spine in the BACKGROUND. The FE
        polls GET /studio/run/{id} to render each per-agent step as it lands (live
        progress), instead of waiting ~60s for a batch reveal. NOTHING is sent — every
        output is HELD/PENDING behind approve-first (publishing still requires the
        separate stage_publish approval gate)."""
        from fastapi.responses import JSONResponse

        dsn = get_dsn()
        try:
            payload = json.loads(await request.body() or b"{}")
        except Exception:
            payload = {}
        session_id = (
            payload.get("sessionId")
            or payload.get("threadId")
            or request.query_params.get("session_id")
            or "studio-default"
        )
        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")

        plan = _load_plan(session_id, dsn)
        override = payload.get("plan")
        if isinstance(override, dict):
            try:
                plan = CampaignPlan.model_validate({**plan.model_dump(), **override})
            except Exception:
                pass

        info = await launch_studio_run(app, dsn, session_id, tenant_id, plan)
        return JSONResponse(
            {
                "ok": True,
                "runId": info["runId"],
                "campaignId": info["campaignId"],
                "status": info["status"],
            }
        )

    @app.get("/studio/run/{run_id}")
    async def studio_run_state(run_id: str):  # noqa: ANN202
        """Poll one run: {status, steps[role,model,input,output], nPending, archetype}.
        Steps come from agent_runs (written incrementally by the spine), so the FE
        renders each agent as it lands. Works for a run already completed in the DB too."""
        from fastapi.responses import JSONResponse

        dsn = get_dsn()
        reg = runs_registry.get(run_id)

        def _load() -> dict:
            import psycopg
            from psycopg.rows import dict_row

            steps: list[dict] = []
            runs_status = None
            n_pending = None
            pending_actions: list[dict] = []
            with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as c:
                rows = c.execute(
                    "SELECT role, model, input, output, created_at FROM agent_runs "
                    "WHERE run_id=%s ORDER BY created_at",
                    (run_id,),
                ).fetchall()
                for i, ar in enumerate(rows):
                    ca = ar.get("created_at")
                    steps.append({
                        "seq": i,
                        "role": ar.get("role"),
                        "model": ar.get("model"),
                        "input": ar.get("input"),
                        "output": ar.get("output"),
                        "createdAt": ca.isoformat() if hasattr(ca, "isoformat") else str(ca),
                    })
                try:
                    row = c.execute(
                        "SELECT status FROM runs WHERE run_id=%s", (run_id,)
                    ).fetchone()
                    runs_status = str(row["status"]).lower() if row else None
                except Exception:
                    runs_status = None
                try:
                    pr = c.execute(
                        "SELECT count(*) n FROM actions WHERE run_id=%s AND status='pending'",
                        (run_id,),
                    ).fetchone()
                    n_pending = pr["n"] if pr else None
                except Exception:
                    n_pending = None
                # The actual HELD draft rows for this run — the result/review surface
                # renders one Approve / Reject / Deep-Review card per row. Real data
                # only (id + idempotency_key drive the existing approve mutation); an
                # empty list is honest (no drafts staged yet / older run).
                try:
                    pend = c.execute(
                        "SELECT id, channel, target, subject, draft, idempotency_key, status "
                        "FROM actions WHERE run_id=%s AND status='pending' ORDER BY created_at",
                        (run_id,),
                    ).fetchall()
                    for ar in pend:
                        draft_txt = ar.get("draft") or ""
                        pending_actions.append({
                            "id": ar.get("id"),
                            "channel": ar.get("channel"),
                            "target": ar.get("target"),
                            "subject": ar.get("subject"),
                            "draft": draft_txt,
                            "idempotencyKey": ar.get("idempotency_key"),
                            "status": ar.get("status"),
                        })
                except Exception:
                    pending_actions = []

            status = None
            archetype = None
            if reg:
                status = reg.get("status")
                if reg.get("summary"):
                    archetype = reg["summary"].get("archetype_id")
                    if reg["summary"].get("n_pending") is not None:
                        n_pending = reg["summary"]["n_pending"]
            if status is None:
                status = (
                    "completed"
                    if runs_status in ("completed", "success")
                    else ("running" if steps else "unknown")
                )
            return {
                "status": status,
                "steps": steps,
                "nPending": n_pending,
                "pending": pending_actions,
                "archetype": archetype,
                "error": reg.get("error") if reg else None,
            }

        data = await asyncio.to_thread(_load)
        return JSONResponse({"ok": True, "runId": run_id, **data})

    @app.post("/studio/interview")
    async def studio_interview_route(request: Request):  # noqa: ANN202
        """The Agency-page INTERVIEW GATE (P1a). The supervisor must gather enough
        context BEFORE a run can start — this route applies the operator's answers to
        the session plan and returns the authoritative gate state (armed / missing /
        next question / ready message). It is the server-side decision; the client
        only renders it and gates the Run button on ``armed``. NOTHING is launched
        here — arming merely UNLOCKS the existing held ``POST /studio/run`` button.

        Body: ``{sessionId, fields:{field:value}}`` (or a single ``{field, value}``).
        A POST with no fields just reads the current gate state."""
        from fastapi.responses import JSONResponse

        from studio.interview import apply_fields, interview_state

        dsn = get_dsn()
        try:
            payload = json.loads(await request.body() or b"{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        session_id = (
            payload.get("sessionId")
            or payload.get("threadId")
            or request.query_params.get("session_id")
            or "studio-default"
        )
        fields = payload.get("fields")
        if not isinstance(fields, dict):
            # accept a single {field, value} shape too
            single = payload.get("field")
            fields = {single: payload.get("value")} if single else {}

        def _apply() -> CampaignPlan:
            plan = _load_plan(session_id, dsn)
            apply_fields(plan, fields)
            if fields:  # only persist when the operator actually answered something
                _persist_plan(dsn, session_id, plan)
            return plan

        try:
            plan = await asyncio.to_thread(_apply)
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status_code=500
            )
        state = interview_state(plan)
        return JSONResponse({"ok": True, "plan": plan.model_dump(), **state})

    @app.post("/studio/upload")
    async def studio_upload_route(request: Request):  # noqa: ANN202
        """Parse an uploaded customers CSV, ingest it, and ATTACH it to the supervisor.

        REAL parse of the bytes the operator picked (row count + columns + sample),
        then: (1) upsert the rows into ``customers`` so the research tools can find
        them, and (2) attach a real summary onto the session plan so the SUPERVISOR
        genuinely sees the list (`_customers_context` renders it every turn) and the
        interview's lead_count is answered from the real row count. Honest throughout:
        ingestion / attach failures are reported, never hidden, and nothing is sent.
        Accepts JSON ``{sessionId, filename, content}`` or a raw ``text/csv`` body."""
        from fastapi.responses import JSONResponse

        dsn = get_dsn()  # previously undefined here -> ingest + attach both NameError'd
        raw = await request.body()
        filename = request.query_params.get("filename") or "upload.csv"
        content = ""
        try:
            payload = json.loads(raw or b"{}")
        except Exception:
            payload = None
        if isinstance(payload, dict):
            content = payload.get("content") or ""
            filename = payload.get("filename") or filename
        else:
            content = raw.decode("utf-8", "replace")

        session_id = (
            (payload.get("sessionId") or payload.get("threadId") if isinstance(payload, dict) else None)
            or request.query_params.get("session_id")
            or "studio-default"
        )

        try:
            result = parse_customers_csv(content, filename)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        # INGEST: upsert parsed leads into ``customers`` (keyed on tenant+email) so
        # ``research_lead`` / ``research_and_stage_leads`` can find them. Idempotent —
        # re-uploading already-seeded leads matches them and creates no duplicates.
        # Honest: if ingestion fails we still return the parse preview with the error.
        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")
        try:
            from studio.customer_research import ingest_leads

            # Re-parse ALL data rows (parse_customers_csv samples only the first 5).
            import csv as _csv
            import io as _io

            reader = _csv.DictReader(_io.StringIO((content or "").lstrip("﻿")))
            rows = [{(k or "").strip(): (v or "") for k, v in r.items()} for r in reader]
            ingest = await asyncio.to_thread(
                lambda: ingest_leads(tenant_id, rows, dsn=dsn)
            )
            result["ingested"] = True
            result["ingest"] = ingest
        except Exception as exc:  # honest: report the failure, keep the preview
            result["ingested"] = False
            result["ingest_error"] = f"{type(exc).__name__}: {exc}"

        # ATTACH the real parse summary onto the session plan so the SUPERVISOR can SEE
        # the uploaded list (rendered by `_customers_context` on every turn) and so the
        # interview's lead_count is answered from the real row count — the same way
        # brand notes are surfaced. Honest: only a real parse is stored; if no rows
        # parsed nothing is attached, and a persistence hiccup is reported, not hidden.
        try:
            def _attach_customers() -> None:
                plan = _load_plan(session_id, dsn)
                # Capture the ingested customer_ids so the provided-leads run can target
                # EXACTLY these rows (compliance: only the operator's own leads).
                ingest_info = result.get("ingest") or {}
                cust_ids = list(ingest_info.get("customer_ids") or [])
                plan.customers = {
                    "filename": result.get("filename"),
                    "rows": int(result.get("rows") or 0),
                    "columns": list(result.get("columns") or []),
                    "sample": list(result.get("sample") or []),
                    "ingested": bool(result.get("ingested")),
                    "customer_ids": cust_ids,
                }
                if result.get("rows"):
                    plan.lead_count = int(result["rows"])
                _persist_plan(dsn, session_id, plan)

            if result.get("rows"):
                await asyncio.to_thread(_attach_customers)
                result["attachedToPlan"] = True
            else:
                result["attachedToPlan"] = False
        except Exception as exc:  # honest: report, never claim the supervisor can see it
            result["attachedToPlan"] = False
            result["attach_error"] = f"{type(exc).__name__}: {exc}"
        return JSONResponse(result)

    @app.post("/studio/notes")
    async def studio_notes_route(request: Request):  # noqa: ANN202
        """Attach operator brand / strategy notes (an uploaded text file) to the
        session plan as REAL context.

        The notes are stored on the session's persisted ``CampaignPlan.notes``, so the
        Host reads them on every turn (``_plan_context``) AND the deterministic run
        loads them with the plan — it is genuine planning/run context, not a badge.
        Accepts JSON ``{sessionId, filename, content}``. NOTHING is sent."""
        from fastapi.responses import JSONResponse

        dsn = get_dsn()
        try:
            payload = json.loads(await request.body() or b"{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        session_id = (
            payload.get("sessionId")
            or payload.get("threadId")
            or request.query_params.get("session_id")
            or "studio-default"
        )
        filename = payload.get("filename") or "notes.txt"
        content = (payload.get("content") or "").strip()
        if not content:
            return JSONResponse(
                {"ok": False, "error": "empty notes — no text content"}, status_code=400
            )

        def _attach() -> int:
            plan = _load_plan(session_id, dsn)
            plan.notes = content
            _persist_plan(dsn, session_id, plan)
            return len(content)

        try:
            chars = await asyncio.to_thread(_attach)
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status_code=500
            )
        return JSONResponse(
            {
                "ok": True,
                "filename": filename,
                "chars": chars,
                "note": "Brand / strategy notes attached to the plan — the team reads "
                "them when planning and running. Nothing was sent.",
            }
        )

    @app.get("/studio/action/{action_id}/evidence")
    async def studio_action_evidence_route(action_id: str):  # noqa: ANN202
        """The REAL, real-only provenance for ONE staged draft: the Brand Voice it
        wrote in, the Customer/CSV facts it used, Lead Memory, Internal Notes, the
        Research Source URLs it cited, Tool Calls, the producing agent + reasoning,
        and the Critic / Jury verdicts — assembled by joining the draft's own run_id
        (and lead) against agent_runs / research_sources / memories. Only genuinely
        used sources appear; an absent category is omitted. 404 if no such action."""
        from fastapi.responses import JSONResponse

        from studio.evidence import build_action_evidence

        dsn = get_dsn()
        try:
            ev = await asyncio.to_thread(build_action_evidence, action_id, dsn=dsn)
        except Exception as exc:
            return JSONResponse(
                {"error": f"{type(exc).__name__}: {exc}"}, status_code=500
            )
        if ev is None:
            return JSONResponse({"error": "no such action"}, status_code=404)
        return JSONResponse(ev.model_dump(by_alias=True))

    @app.get("/studio/campaign/{run_id}/classify")
    async def studio_campaign_classify_route(run_id: str):  # noqa: ANN202
        """READ-ONLY: split a campaign run's PENDING drafts into ``eligible`` (safe to
        batch-send — computed confidence at/above threshold, no safety/gate/quality
        escalation) and ``review_required`` (everything else, fail-closed). Sends
        nothing."""
        from fastapi.responses import JSONResponse

        from studio.campaign_send import classify_campaign

        dsn = get_dsn()
        try:
            out = await asyncio.to_thread(classify_campaign, run_id=run_id, dsn=dsn)
        except Exception as exc:
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)
        return JSONResponse(out)

    @app.post("/studio/campaign/{run_id}/send-eligible")
    async def studio_campaign_send_eligible_route(run_id: str, request: Request):  # noqa: ANN202
        """OPERATOR-INITIATED: send ONLY the eligible/safe drafts of a campaign run.
        Each goes through the existing per-draft ``approve_and_publish`` (atomic
        exactly-once claim + gmail allow-list/redirect) — NOT a bulk bypass.
        Non-eligible drafts are returned under ``skipped`` for review, never sent."""
        from fastapi.responses import JSONResponse

        from studio.campaign_send import send_eligible

        try:
            payload = json.loads(await request.body() or b"{}")
        except Exception:
            payload = {}
        operator = (payload or {}).get("operator") if isinstance(payload, dict) else None
        dsn = get_dsn()
        try:
            out = await asyncio.to_thread(
                send_eligible, run_id=run_id, dsn=dsn, operator=operator
            )
        except Exception as exc:
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)
        return JSONResponse(out)

    @app.post("/studio/campaign/action/{action_id}/override")
    async def studio_campaign_override_route(action_id: str, request: Request):  # noqa: ANN202
        """OVERRIDE one specific draft past the eligibility gate. REQUIRES an explicit
        ``reason``; writes an ``override`` audit row BEFORE the send and routes the
        send through the SAME ``approve_and_publish`` (exactly-once + allow-list still
        apply). 400 if no reason is given — never a bare force-send."""
        from fastapi.responses import JSONResponse

        from studio.campaign_send import OverrideRequiresReasonError, override_send

        try:
            payload = json.loads(await request.body() or b"{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        reason = (payload.get("reason") or "").strip()
        operator = payload.get("operator")
        if not reason:
            return JSONResponse(
                {"ok": False, "error": "override requires an explicit reason"},
                status_code=400,
            )
        dsn = get_dsn()
        try:
            out = await asyncio.to_thread(
                override_send, action_id, reason=reason, operator=operator, dsn=dsn
            )
        except OverrideRequiresReasonError:
            return JSONResponse(
                {"ok": False, "error": "override requires an explicit reason"},
                status_code=400,
            )
        except Exception as exc:
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)
        return JSONResponse({"ok": True, **out})
