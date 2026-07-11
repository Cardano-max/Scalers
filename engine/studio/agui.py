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
import hashlib
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
# The REAL model the compose/brainstorm jury runs (Agent(JURY_MODEL).run). The
# provided-leads staged-count check is pure code and records DETERMINISTIC_JURY_MODEL
# from autonomy.jury instead (65w.15) — never this id.
JURY_MODEL = "anthropic:claude-sonnet-4-5"


def _draft_quality_conf(verdict: str | None, confidence: float | None) -> float | None:
    """Map the per-draft critic's verdict + its OWN confidence into a single ship-quality
    score for the action's ``conf`` field — what the Review Queue shows per draft.

    The critic's raw confidence is "how sure it is of its verdict", so it cannot be
    persisted directly (a confidently-rejected draft would read as high-confidence). We
    fold verdict + confidence into a quality band: a confidently-approved, well-grounded
    draft lands high; a draft the critic flags for revision/rejection lands low — so the
    queue shows REAL, VARYING confidence instead of a flat None. A critic that could not
    judge (error / unknown verdict) yields None (honest unknown), never a fabricated score."""
    if verdict is None or confidence is None:
        return None
    c = max(0.0, min(1.0, float(confidence)))
    v = verdict.strip().lower()
    if v == "approve":
        return round(0.70 + 0.30 * c, 3)
    if v == "revise":
        return round(0.60 - 0.30 * c, 3)
    if v == "reject":
        return round(0.30 - 0.30 * c, 3)
    return None  # error / unknown verdict -> honest unknown


# --------------------------------------------------------------------------- #
# Model-failure circuit breaker (operator defect: a 3-draft ask ran the whole
# roster while EVERY model call failed 400, staging junk template drafts).
# --------------------------------------------------------------------------- #
# How many CONSECUTIVE per-lead model/HTTP failures — with the strategist ALSO
# failed — stop the per-lead loop instead of grinding the whole cohort.
MODEL_FAILURE_BREAKER_THRESHOLD = 5

# Exception type names that prove a REAL model call was attempted and rejected/
# failed at the provider/HTTP layer (unlike a missing-key/config error, where the
# cell never attempts a call — the deterministic-fallback case).
_MODEL_ERROR_NAMES = ("ModelHTTPError", "APIStatusError", "HTTPStatusError")


def _is_model_error(exc: BaseException | None) -> bool:
    """True iff ``exc`` (or an exception in its explicit ``__cause__`` chain — cells
    wrap provider errors in ``CellExecutionError``) is a REAL model/HTTP failure: an
    attempted provider call that failed (e.g. pydantic-ai's ``ModelHTTPError`` for a
    400/402/429/5xx). A no-key / config error never attempted a call, so it is NOT a
    model error — deterministic fallbacks must never trip the circuit breaker."""
    try:
        from pydantic_ai.exceptions import ModelHTTPError

        _http_types: tuple[type, ...] = (ModelHTTPError,)
    except Exception:  # pragma: no cover - pydantic_ai is a hard dependency
        _http_types = ()
    hops = 0
    while exc is not None and hops < 8:
        if _http_types and isinstance(exc, _http_types):
            return True
        if type(exc).__name__ in _MODEL_ERROR_NAMES:
            return True
        if isinstance(getattr(exc, "status_code", None), int):
            return True
        exc = exc.__cause__
        hops += 1
    return False


def _draft_model_fallback(draft: dict[str, Any]) -> bool:
    """True iff this draft's grounding shows the copywriter cell ATTEMPTED a real
    model call that failed at the model/HTTP layer, so the copy fell back to the
    deterministic template (``copy=deterministic_fallback(ModelHTTPError)``). A pure
    no-key template (``copy=deterministic_template`` — no call attempted) is False."""
    for g in draft.get("grounding") or []:
        s = str(g)
        if s.startswith("copy=deterministic_fallback(") and any(
            name in s for name in _MODEL_ERROR_NAMES
        ):
            return True
    return False


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
    # The offer / call-to-action the message drives toward (a booking link, a promo, or
    # "reply to book"). A gating field — a campaign with no ask is half a campaign.
    offer: str = ""
    # --- P1-B (executive discovery) optional refinements (never gate a run) -------- #
    # offer_type: a TYPED CTA menu (booking / consult / flash / discount / touch-up /
    # artist-spotlight) — the structured counterpart to the free-text ``offer``.
    # segment: which lifecycle bucket we're addressing (cold / warm / past / recurring).
    # no_convert_reason: the operator's read on WHY these leads didn't book (free text).
    # prior_contact: what prior contact / conversation there has been (free text).
    offer_type: str = ""
    segment: str = ""
    no_convert_reason: str = ""
    prior_contact: str = ""
    # --- P1-D (enrich the confirmable spec) optional fields (never gate a run) ----- #
    # brand_voice: how it should sound / brand-voice notes for THIS campaign.
    # research_depth: how deep to research (light / standard / deep).
    # personalization_rules: what personalization is OK (free text — honest guardrails).
    # do_not_use: anything the drafts must NOT say / mention (free text).
    # success_criteria: what would make this campaign a win (free text).
    brand_voice: str = ""
    research_depth: str = ""
    personalization_rules: str = ""
    do_not_use: str = ""
    success_criteria: str = ""
    # per_lead: one personalized message per lead (True / default) vs one shared message
    # (False). personalize: tailor each message from the lead's history + profile (True /
    # default). Both surface in the plan summary; None = unanswered (sensible default).
    per_lead: bool | None = None
    personalize: bool | None = None
    # Lead source (hard compliance branch): "provided" = use ONLY the operator's own
    # leads (uploaded CSV / existing DB), researched per-lead; "source_new" = find new
    # prospects on the web. Empty = not chosen yet. Drives the orchestration mode.
    lead_source: str = ""
    # --- P1 (tattoo pivot) optional refinements (never gate a run) ---------------- #
    # target_category: which customer cohort to focus on (new/artist-specific/unpaid/
    # recurring/reactivation/all); scope: one artist / one shop / whole studio;
    # use_conversation_history: read each lead's prior messages for the psych analyst;
    # attach_artwork: match the right artist artwork where it fits (P4-gated capability).
    target_category: str = ""
    scope: str = ""
    use_conversation_history: bool | None = None
    attach_artwork: bool | None = None
    # --- ju1.3 skindesign campaign-creation interview refinements (never gate a run) -- #
    # artist: which artist this campaign fronts (matches the studio's real roster);
    # location: which studio/location to target; reference_campaign: reuse a prior real
    # campaign example's STYLE as the reference (grounded in ju1.2's example library);
    # payment_plan: the operator's exact payment-plan wording (e.g. "Klarna & Affirm");
    # test_mode: the operator's stated preference to KEEP the server-side TEST-MODE send
    # gate on — DISPLAY ONLY. The authoritative gate is ju1.1's server-side tenant flag;
    # this field never turns it off (the console cannot disable the send gate).
    artist: str = ""
    location: str = ""
    reference_campaign: bool | None = None
    payment_plan: str = ""
    test_mode: bool | None = None
    # P1.5: the objection the plan ASSUMES dominates this cohort — the recorded assumption
    # the progress-aware replan (progress_board.maybe_replan) tests against the analyst's
    # MEASURED dominant objection. Populated by the planner (plan_campaign) from
    # target_category / campaign_type; empty = no assumption (replan then cannot fire).
    assumed_objection: str = ""
    # Uploaded customer list — a REAL parse of the operator's CSV ({filename, rows,
    # columns, sample, ingested}). Persisted with the plan and surfaced to the
    # supervisor on every turn (see `_customers_context`) so it can truthfully say
    # "I see your CSV, N rows: col, col" and reason over the rows. Empty = no CSV
    # uploaded (the supervisor must NOT pretend a list exists).
    customers: dict[str, Any] = Field(default_factory=dict)
    # Explicit per-lead targets picked in chat/voice (emails, or exact names when a
    # lead has no email) — e.g. the three price-objection leads chosen via
    # `list_conversation_leads`. The provided-leads executor resolves THESE first
    # (before the uploaded-CSV ids and the DB cohort), so "run the full team on
    # exactly these people" is a plan state, not a lucky cohort overlap.
    leads: list[str] = Field(default_factory=list)


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
    "the campaign (or approves the plan to run), call `run_campaign` ONCE. It "
    "LAUNCHES the real multi-agent spine (research -> strategy -> drafts -> "
    "critique -> jury) in the background and returns the run id IMMEDIATELY; the "
    "run's drafts stage HELD for approval and NOTHING is sent. Reply right away "
    "with the run id and point the operator at the Agency tab to watch each "
    "agent's step live — do NOT claim drafts exist yet and do NOT invent results "
    "(the run posts its honest summary to this thread when it finishes).\n"
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
    "the tool returns — never invent a customer detail. When the operator says to "
    "pick the cohort from the IMPORTED CONVERSATIONS / 'their real threads' (e.g. "
    "'customers who stepped back over price or timing'), call "
    "`list_conversation_leads` (topic='price'/'timing'/…) — the verbatim threads "
    "live in the DATABASE (they are not files), and the campaign run's cohort "
    "already prioritizes them. NEVER reply that you have no conversation history "
    "without calling that tool first.\n"
    "4b. ONE SPINE PER ASK — for a per-lead CAMPAIGN on picked leads: "
    "`list_conversation_leads` → `revise_plan(leads=[their emails], "
    "lead_source='provided', per_lead=true, channels=[the operator's channel], "
    "lead_count=N, output_count=N, campaign_type='outreach', deep_research=…, "
    "offer=…)` → `run_campaign`. The full team (researcher → analyst → "
    "copywriter → critic → jury) then runs per lead, live in the Agency tab, and "
    "stages EXACTLY N drafts. Do NOT also call `research_and_stage_leads` for "
    "the same ask — that is the lightweight NO-TEAM path (quick staging without "
    "the live per-agent run) and calling both stages duplicates. Never launch "
    "`run_campaign` without first setting the plan fields to what the operator "
    "just asked — an unset plan runs the session's stale configuration.\n"
    "5. NEVER send or publish anything yourself. If the operator wants something "
    "posted/emailed, call `stage_publish` — it stages a PENDING action that a "
    "human must approve; it is held, never sent.\n"
    "5b. LIVE STATE: when the operator asks which leads a run finalized/targeted, "
    "what the agents are doing right now, what files or images exist ('I added a "
    "new tattoo design, can you check which one it is?'), which artworks an artist "
    "has, or what changed recently for an artist — call the matching live-state "
    "tool (`get_run_leads`, `get_agent_activity`, `get_uploaded_files`, "
    "`get_artist_artworks`, `get_artist_memory`). These read the database fresh "
    "on every call; NEVER answer such questions from memory or guess.\n"
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
def _data_inventory_context(ctx: RunContext[StudioDeps]) -> str:
    """Surface the HONEST data inventory (live DB counts + what's missing) on every turn
    (ju1.3, anti-theater core). Before planning, the supervisor must state exactly what
    real data it has and does NOT have — customers/artists/studios/examples counted live
    from the DB (never hardcoded) plus the honest missing-data sentence. Built by the ONE
    shared builder the voice supervisor also calls, so chat and voice cannot diverge.
    Best-effort: an unreadable store yields the honest can't-read line, never a fake count."""
    try:
        from studio.inventory import build_data_inventory

        return build_data_inventory(ctx.deps.tenant_id, dsn=ctx.deps.dsn)
    except Exception:
        return ""


@studio_agent.instructions
def _live_operations_context(ctx: RunContext[StudioDeps]) -> str:
    """Inject the LIVE review-queue + run state on EVERY turn (anti-fabrication).

    A browser-driven audit caught the host answering "the review queue is empty —
    0 drafts" while 7 pending drafts sat in the DB (and on the sidebar badge of
    the same page). Instructions alone decay; this makes fabrication structurally
    unnecessary: the true numbers are already in context, computed by SQL, and
    the host is told these are the ONLY operational numbers it may state.
    Best-effort: an unreadable store yields the honest can't-read line, never a
    guessed count. Built by the ONE shared builder the voice supervisor also
    injects, so chat and voice cannot diverge."""
    try:
        from studio.inventory import live_operations_block

        return live_operations_block(ctx.deps.tenant_id, dsn=ctx.deps.dsn)
    except Exception:
        return (
            "LIVE OPERATIONS STATE unavailable this turn — say so if asked about "
            "the queue; never guess counts."
        )


@studio_agent.instructions
def _interview_checklist_context(ctx: RunContext[StudioDeps]) -> str:
    """Surface the canonical 10-question campaign-creation interview (ju1.3) so the host
    asks the right questions after the data readback. The SAME block the voice supervisor
    renders, so both surfaces ask identical questions."""
    try:
        from studio.interview import campaign_interview_prompt

        return campaign_interview_prompt()
    except Exception:
        return ""


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
        lines.append("MEMORIES: memory layer present; no memories loaded this turn.")
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
    # P1-A: the honest semantic read of the file — a natural summary (real counts) the
    # supervisor should STATE to the operator, plus the column roles + any columns we
    # could not map. HONESTY: this is a REAL profile of the uploaded rows; never invent a
    # segment/objection/social count beyond it, and name unknown columns rather than guess.
    summary = str(c.get("summary") or "").strip()
    if summary:
        lines.append(
            "- semantic summary (REAL counts from the rows — say this back to the "
            f"operator, verbatim numbers): {summary}"
        )
    profile = c.get("profile") or {}
    roles = profile.get("column_roles") if isinstance(profile, dict) else None
    if roles:
        role_bits = ", ".join(f"{col} → {role}" for col, role in roles.items())
        lines.append(f"- column roles I recognized: {role_bits}")
    unknown = profile.get("unknown_columns") if isinstance(profile, dict) else None
    if unknown:
        lines.append(
            "- columns I could NOT map (do not guess what they mean): "
            + ", ".join(str(u) for u in unknown)
        )
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

        # Resolve for THIS run's tenant (never a fixture default) so the snippet is the
        # real tenant's voice or honestly absent — never another studio's (r8).
        brand_voice, _claims = resolve_brand_voice(ctx.deps.tenant_id)
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


@studio_agent.instructions
def _artifacts_context_instruction(ctx: RunContext[StudioDeps]) -> str:
    """Surface the UNIVERSAL uploaded-file registry to the host on EVERY turn (nmh.4),
    so it can truthfully answer "can you see my CSV / brand voice / artwork — how many
    images?" and the run's agents can ground on the parsed content of every uploaded
    file. Best-effort + honest-empty (never claims a file it does not have)."""
    try:
        from studio.artifacts import build_artifacts_context

        return build_artifacts_context(ctx.deps.tenant_id, dsn=ctx.deps.dsn)
    except Exception:
        return ""


def _register_document_artifact(
    tenant_id: str,
    name: str,
    artifact_type: str,
    content: str,
    *,
    summary: str | None,
    document_id: str,
    dsn: str | None,
) -> str:
    """Register an uploaded text document (brand voice / doc / pdf) as a universal
    context artifact linked to its ``tenant_documents`` row. Deterministic id off the
    doc id so re-uploading the same doc refreshes rather than duplicates."""
    from studio.artifacts import register_artifact

    return register_artifact(
        tenant_id,
        name,
        artifact_type,
        media_type="text/markdown" if artifact_type != "pdf" else "application/pdf",
        summary=summary,
        parsed_content=content,
        source="upload",
        meta={"document_id": document_id},
        artifact_id=f"art_doc_{document_id}",
        dsn=dsn,
    )


def build_documents_context(tenant_id: str, plan: CampaignPlan, dsn: str | None) -> str:
    """Assemble the host's per-turn view of the PERSISTENT tenant document store.

    This is what kills "I don't have access to any uploaded documents": the active
    docs (a real index of name + summary) are listed so the host can truthfully answer
    "do you have my documents?", AND the passages most relevant to the current plan are
    retrieved (ts_rank) and injected so it can actually reason over their content this
    turn. HONESTY in both directions: with NO active docs it says so plainly (so the
    host never pretends a doc exists); the store being unreachable degrades to a neutral
    note, never a false claim. Pure-ish (reads the store) so it is unit-testable."""
    try:
        from studio import documents as docstore

        docs = docstore.active_docs_index(tenant_id, dsn=dsn)
    except Exception:
        return (
            "TENANT DOCUMENT STORE: the persistent knowledge store is configured; "
            "documents uploaded in the Knowledge panel survive across sessions and the "
            "whole team reads them. (No documents loaded this turn.)"
        )
    if not docs:
        return (
            "TENANT DOCUMENT STORE: you currently have NO uploaded documents for this "
            "studio. If the operator asks whether you have their documents / brand "
            "playbook, say honestly that none are uploaded yet and invite them to add "
            "one in the Knowledge panel. NEVER claim to have a document you do not."
        )
    lines = [
        "TENANT DOCUMENT STORE — you HAVE these persistent uploaded documents and you "
        "ARE using them (the whole team reads them, RAG-grounded). When the operator "
        "asks 'do you have my documents?', answer YES and list them by name:",
    ]
    for doc in docs:
        summ = (doc.get("summary") or "").strip()
        meta = f"{doc.get('kind', 'doc')}, {doc.get('chars', 0)} chars"
        lines.append(f"- {doc.get('name')} ({meta})" + (f": {summ}" if summ else ""))
    # Retrieve passages relevant to the current plan so the host can reason over real
    # content this turn (not just names). Best-effort + honest-empty.
    query = " ".join(
        x for x in [plan.goal, plan.audience, " ".join(plan.channels or [])] if x
    ).strip()
    try:
        from studio import documents as docstore

        hits = docstore.retrieve(tenant_id, query, k=4, dsn=dsn) if query else []
    except Exception:
        hits = []
    if hits:
        lines.append(
            "\nRELEVANT PASSAGES (retrieved from your documents for the current plan — "
            "ground brand/strategy claims in these and cite by document + section; never "
            "invent beyond them):"
        )
        for h in hits:
            head = (h.get("heading") or "").strip()
            label = h.get("doc_name") or "document"
            cite = f"{label} › {head}" if head else label
            lines.append(f"  - [{cite}] {(h.get('content') or '')[:500]}")
    return "\n".join(lines)


@studio_agent.instructions
def _documents_context(ctx: RunContext[StudioDeps]) -> str:
    """Surface the persistent tenant document store to the host on EVERY turn."""
    return build_documents_context(ctx.deps.tenant_id, ctx.deps.state, ctx.deps.dsn)


# --------------------------------------------------------------------------- #
# Live PROGRESS summary — the active run's REAL state injected into the host
# context, mirroring the document-store injection above. This is what lets the
# supervisor answer "what's the progress?" from the actual runs / agent_runs /
# actions of the active run instead of guessing. Honest-empty in both directions:
# no active run -> say so (all zero); no data -> report real zeros, never invent.
# --------------------------------------------------------------------------- #

# Map a node span's execution status onto the agent-progress bucket the host
# reports. Unknown statuses fall through to their own raw name (honest — a status
# the data shows is never silently dropped or relabelled).
_SPAN_STATUS_BUCKET: dict[str, str] = {
    "ok": "completed",
    "completed": "completed",
    "success": "completed",
    "done": "completed",
    "failed": "failed",
    "error": "failed",
    "running": "running",
    "in_progress": "running",
    "queued": "queued",
    "pending": "queued",
    "skipped": "skipped",
    "needs-review": "needs_review",
    "needs_review": "needs_review",
}


def _tenant_runs(tenant_id: str, dsn: str | None) -> list[Any]:
    """REAL read: this tenant's runs, newest first (or [] on any failure). A thin,
    monkeypatchable seam over the durable run store."""
    try:
        from harness.runstore import PostgresRunStore

        if not dsn:
            return []
        store = PostgresRunStore(dsn)
        store.setup()
        return list(store.list_runs(tenant_id))
    except Exception:
        return []


def _tenant_actions(tenant_id: str, dsn: str | None) -> list[Any]:
    """REAL read: this tenant's actions (review-queue / activity rows), newest first
    (or [] on failure). Monkeypatchable seam over the actions store."""
    try:
        from actions.store import list_actions

        return list(list_actions(tenant_id, dsn=dsn))
    except Exception:
        return []


def _agent_runs_for(run_id: str, dsn: str | None) -> list[dict[str, Any]]:
    """REAL read: the per-role agent_runs recorded for one run, oldest first (or []
    on failure). Monkeypatchable seam over the team store."""
    try:
        from team.store import TeamStore

        if not dsn:
            return []
        ts = TeamStore(dsn)
        ts.setup()
        return list(ts.list_agent_runs(run_id))
    except Exception:
        return []


def build_progress_context(tenant_id: str, plan: CampaignPlan, dsn: str | None) -> str:
    """Assemble the host's per-turn view of the ACTIVE campaign run's REAL progress.

    This is what lets the supervisor truthfully answer "what's the progress?": it
    resolves the most recent campaign run for this tenant and reports the genuine
    counts read from runs / agent_runs / actions — agents by status (completed /
    running / queued / skipped / failed), drafts created vs expected, research
    sources found, review-queue items, and sends completed / failed. HONESTY in both
    directions: with NO run it says so plainly (all zero, nothing launched) so the
    host never invents progress; a store hiccup degrades to that honest-empty note,
    never a fabricated number. Pure-ish (reads the stores through the monkeypatchable
    seams above) so it is unit-testable without a database."""
    runs = _tenant_runs(tenant_id, dsn)
    actions_all = _tenant_actions(tenant_id, dsn)

    # Resolve the ACTIVE run. The latest ``runs`` row is authoritative (it carries the
    # run status + the per-agent node spans). But a studio run materializes its runs
    # row only at the END, so an in-flight run shows up in ``actions`` first — if the
    # newest action points at a run that has NO runs row yet, surface that in-flight
    # run instead. Factored into ``progress_board.resolve_active_run`` so the durable
    # board and this textual view share ONE run-resolution implementation.
    from studio.progress_board import resolve_active_run

    run_id, record = resolve_active_run(runs, actions_all)

    if not run_id:
        return (
            "CAMPAIGN PROGRESS: there is NO active campaign run for this studio yet — "
            "nothing has been launched, so every count is zero. If the operator asks "
            "'what's the progress?', say honestly that no run is in flight and offer to "
            "start one. NEVER invent progress, drafts, research, or sends."
        )

    # Agents by status. The authoritative source is the run's per-node spans (each
    # carries a real execution status). Before the runs row materializes we fall back
    # to the recorded agent_runs (each recorded run = one completed agent) — honest,
    # and we never invent queued/skipped agents the data does not actually show.
    agent_runs = _agent_runs_for(run_id, dsn)
    node_spans = [
        s
        for s in (getattr(record, "steps", None) or [])
        if (getattr(s, "kind", "node") or "node") == "node"
    ]
    status_counts: dict[str, int] = {}
    if node_spans:
        for s in node_spans:
            raw = (getattr(s, "status", "") or "").lower()
            bucket = _SPAN_STATUS_BUCKET.get(raw, raw or "unknown")
            status_counts[bucket] = status_counts.get(bucket, 0) + 1
    elif agent_runs:
        status_counts["completed"] = len(agent_runs)

    # Drafts + research come from the recorded role outputs (agent_runs).
    drafts_created = sum(1 for ar in agent_runs if ar.get("role") == "draft")
    research_sources = 0
    for ar in agent_runs:
        if ar.get("role") != "researcher":
            continue
        out = ar.get("output")
        if not isinstance(out, dict):
            continue
        cited = out.get("cited")
        if cited is None:
            cited = len(out.get("sources") or [])
        try:
            research_sources += int(cited or 0)
        except (TypeError, ValueError):
            pass

    expected = plan.output_count or plan.lead_count or 0

    # Review queue + sends come from THIS run's actions (filtered from the tenant set).
    run_actions = [a for a in actions_all if getattr(a, "run_id", None) == run_id]
    review_queue = sum(1 for a in run_actions if getattr(a, "status", None) == "pending")
    sends_completed = sum(1 for a in run_actions if getattr(a, "status", None) == "sent")
    sends_failed = sum(1 for a in run_actions if getattr(a, "status", None) == "failed")

    if record is not None:
        run_status = getattr(getattr(record, "status", None), "value", None) or str(
            getattr(record, "status", "") or "unknown"
        )
    else:
        run_status = "running"  # in-flight: the runs row is not materialized yet

    # Render only the agent buckets that actually have a count (honest — no zero-fill
    # of statuses the run never produced), in a stable, readable order.
    order = ["completed", "running", "queued", "skipped", "failed", "needs_review"]
    ordered = [b for b in order if status_counts.get(b)]
    ordered += [b for b in status_counts if b not in order and status_counts[b]]
    agents_line = (
        ", ".join(f"{b}={status_counts[b]}" for b in ordered) if ordered else "none recorded yet"
    )
    drafts_line = f"{drafts_created} created" + (f" / {expected} expected" if expected else "")

    lines = [
        "CAMPAIGN PROGRESS — the REAL, live state of the most recent campaign run for "
        "this studio, read from runs / agent_runs / actions. When the operator asks "
        "'what's the progress?', answer from HERE and never fabricate a count:",
        f"- run: {run_id} (status: {run_status})",
        f"- agents: {agents_line}",
        f"- drafts: {drafts_line}",
        f"- research sources found: {research_sources}",
        f"- review queue (drafts staged HELD, approve-first): {review_queue}",
        f"- sends: {sends_completed} completed, {sends_failed} failed",
    ]
    return "\n".join(lines)


@studio_agent.instructions
def _progress_context(ctx: RunContext[StudioDeps]) -> str:
    """Surface the active run's REAL progress to the host on EVERY turn."""
    return build_progress_context(ctx.deps.tenant_id, ctx.deps.state, ctx.deps.dsn)


# --------------------------------------------------------------------------- #
# Live TEAM NARRATION — the host's running commentary while a run executes,
# projected PURELY from the REAL recorded agent_runs (the same per-role steps the
# run_state route returns). One honest line per recorded step: the supervisor only
# narrates a stage that ACTUALLY ran, names the real lead/channel from the step's
# own input, and says so plainly when a step failed. No canned script, no fake
# "lead 8 of 10" totals the data does not support — the timeline IS the data.
# --------------------------------------------------------------------------- #


def _step_failed(output: Any) -> bool:
    """Whether a recorded step's output reads as a genuine failure (honest — a failed
    strategist/critic is narrated as a snag, never as success)."""
    if not isinstance(output, dict):
        return False
    if str(output.get("status", "")).lower() in ("failed", "error"):
        return True
    return str(output.get("verdict", "")).lower() in ("error", "failed")


def _lead_label(step: dict[str, Any]) -> str:
    """The real lead name (or customer id) carried on a step's input, or '' if none."""
    inp = step.get("input") if isinstance(step.get("input"), dict) else {}
    name = inp.get("name") or inp.get("lead") or inp.get("customer_id")
    return str(name).strip() if name else ""


# Per-lead roles whose narration carries an "X of N" progress tag when the planned
# total N is genuinely known. Strategist / jury are one-shot and get no tag.
_PER_LEAD_ROLES = ("researcher", "analyst", "draft", "critic")


def _planned_lead_total(steps: list[dict[str, Any]]) -> int:
    """The REAL planned lead count for this run, read straight from the recorded steps
    (the strategist / jury steps record ``n_leads`` — the actual lead count the run
    operated on). 0 when no step carries it, so the "X of N" framing is dropped only
    when the total is genuinely unknown — never fabricated."""
    for s in steps:
        if not isinstance(s, dict):
            continue
        inp = s.get("input") if isinstance(s.get("input"), dict) else {}
        n = inp.get("n_leads")
        if isinstance(n, bool):  # guard: bool is an int subclass
            continue
        if isinstance(n, int) and n > 0:
            return n
    return 0


def _narration_line(step: dict[str, Any], progress: str = "") -> str:
    """The host-voice narration for ONE recorded step. Pure: reads only the step's own
    role / input / output (plus an already-computed REAL ``progress`` tag like "3 of
    10"), so the line can never describe a stage that did not run."""
    role = str(step.get("role") or "").strip().lower()
    inp = step.get("input") if isinstance(step.get("input"), dict) else {}
    out = step.get("output") if isinstance(step.get("output"), dict) else {}
    failed = _step_failed(step.get("output"))
    lead = _lead_label(step)
    channel = str(inp.get("channel") or "").strip()
    prog = f" ({progress})" if progress else ""

    if role == "strategist":
        if failed:
            return "The strategist hit a snag setting the angle, so the team is drafting straight from your goal."
        angle = str(out.get("target_angle") or out.get("angle") or "").strip()
        return (
            f"The strategist set the campaign angle: “{angle}”."
            if angle
            else "The strategist set the campaign angle for the team."
        )
    if role == "analyst":
        who = lead or "this lead"
        if failed:
            return f"Reading {who}'s history{prog} hit a snag — continuing from what's on file."
        cat = str(out.get("umbrella_category") or "").replace("-", " ").strip()
        obj = str(out.get("primary_objection") or "").strip()
        if obj and obj != "none-found":
            return f"Analyzing {who}{prog} — {cat + ', ' if cat else ''}reading their objection: {obj}."
        return f"Analyzing {who}{prog} — reading where they sit" + (f": {cat}." if cat else ".")
    if role == "researcher":
        who = lead or "this lead"
        if failed:
            return f"Research on {who}{prog} ran into trouble — continuing from what's already on file."
        if out.get("degraded"):
            return f"Researching {who}{prog} — no fresh web sources came back, so drafting from their record."
        return f"Researching {who}{prog} — pulling their history and profile."
    if role == "draft":
        ch = f"{channel} " if channel else ""
        base = (
            f"The copywriter is drafting a personalized {ch}message for {lead}"
            if lead
            else f"The copywriter is drafting a personalized {ch}message"
        )
        return f"{base}{prog}."
    if role == "critic":
        ch = f"{channel} " if channel else ""
        who = f" for {lead}" if lead else ""
        if failed:
            return f"The critic couldn't finish its review on the {ch}draft{who}{prog} — flagged for you to check."
        verdict = str(out.get("verdict") or "").strip()
        return (
            f"The critic reviewed the {ch}draft{who}{prog} — verdict: {verdict}."
            if verdict
            else f"The critic is reviewing the {ch}draft{who}{prog}."
        )
    if role == "jury":
        note = str(out.get("note") or "").strip()
        return (
            f"Wrapping up — {note}"
            if note
            else "Wrapping up — aggregating confidence across the drafts; everything is held for your approval."
        )
    # Honest fallback: a role we don't have bespoke copy for is still narrated, not dropped.
    label = role or "the team"
    return f"{label.capitalize()} step {'failed' if failed else 'completed'}."


def run_narration(steps: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Project the run's REAL recorded steps into host-voice narration — one entry per
    recorded ``agent_run``, in order. Pure + DB-free (takes the already-loaded steps),
    so it is unit-testable and can never narrate a stage that did not actually run.

    Per-lead steps (researcher / draft / critic) carry an "X of N" progress tag when
    the planned total N is genuinely known from the run data: N is the real ``n_leads``
    the strategist / jury recorded, and X is the real count of that role's steps done so
    far. The tag is omitted only when N is genuinely unknown — never invented.

    Each entry: ``{seq, role, line, failed}``."""
    steps = [s for s in (steps or []) if isinstance(s, dict)]
    total = _planned_lead_total(steps)
    role_done: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        role = str(step.get("role") or "").strip().lower()
        progress = ""
        if role in _PER_LEAD_ROLES:
            role_done[role] = role_done.get(role, 0) + 1
            if total:
                progress = f"{role_done[role]} of {total}"
        out.append(
            {
                "seq": step.get("seq", i),
                "role": str(step.get("role") or ""),
                "line": _narration_line(step, progress),
                "failed": _step_failed(step.get("output")),
            }
        )
    return out


def _mirror_failed(run: dict[str, Any]) -> bool:
    """True when one recorded agent_run failed (verdict=error/failed OR an explicit
    failed status on the output)."""
    out = run.get("output") if isinstance(run.get("output"), dict) else {}
    return _step_failed(out) or str(out.get("status") or "").lower() == "failed"


def _mirror_text(role: str, runs: list[dict[str, Any]]) -> str:
    """The ONE client-readable line for all of a role's recorded steps in a run —
    agency voice, real counts, honest failures; never raw JSON, never a stack trace."""
    n = len(runs)
    failed = sum(1 for r in runs if _mirror_failed(r))
    outputs = [r.get("output") if isinstance(r.get("output"), dict) else {} for r in runs]
    fail_note = f" {failed} step(s) hit an error — details in the Runs tab." if failed else ""

    if role == "planner":
        base = next((o for o in outputs if o.get("phase") != "replan"), None)
        parts: list[str] = []
        if base is not None:
            targets = base.get("targets") if isinstance(base.get("targets"), dict) else {}
            who = str(targets.get("description") or targets.get("category") or "").strip()
            quota = (base.get("stop_conditions") or {}).get("total_quota") if isinstance(
                base.get("stop_conditions"), dict) else None
            angle = str(base.get("angle") or "").strip()
            line = f"Planned the campaign — targeting {who or 'the selected audience'}"
            if quota:
                line += f", up to {quota} message(s)"
            if angle:
                line += f"; angle: “{angle}”"
            parts.append(line + ".")
        for o in outputs:
            if o.get("phase") == "replan":
                reason = str((o.get("replan") or {}).get("contradiction") or "").strip()
                parts.append(f"Adjusted the plan mid-run: {reason}." if reason
                             else "Adjusted the plan mid-run from what the leads showed.")
        return " ".join(parts) or "Planned the campaign — the full blueprint is in the Runs tab."
    if role == "strategist":
        if failed == n:
            return "The strategy step hit an error — details in the Runs tab."
        last = outputs[-1]
        angle = str(last.get("primary_angle") or last.get("angle")
                    or last.get("big_idea") or "").strip()
        return (f"Set the campaign angle: “{angle}”." if angle
                else "Set the campaign strategy for the team.") + fail_note
    if role == "researcher":
        cited = sum(int(o.get("cited") or 0) for o in outputs)
        src = f" — {cited} source(s) cited" if cited else ""
        return f"Researched {n} lead(s){src}.{fail_note}"
    if role == "analyst":
        objections = sum(
            1 for o in outputs
            if str(o.get("primary_objection") or "").strip() not in ("", "none-found")
            and str(o.get("status") or "") != "failed"
        )
        obj_note = f"{objections} objection(s) found" if objections else "no objections found"
        return f"Analyzed {n} lead(s) — {obj_note}.{fail_note}"
    if role == "draft":
        chans = sorted({
            str((r.get("input") or {}).get("channel") or "").strip()
            for r in runs if isinstance(r.get("input"), dict)
        } - {""})
        across = f" across {', '.join(chans)}" if chans else ""
        return f"Drafted {n} personalized message(s){across} — all held for your review.{fail_note}"
    if role == "critic":
        if failed:
            return (f"Reviewed {n} draft(s) — {n - failed} passed; {failed} check(s) "
                    f"failed and are flagged for your review (details in the Runs tab).")
        return f"Reviewed {n} draft(s) — all passed."
    if role == "jury":
        note = str(outputs[-1].get("note") or "").strip()
        return note or ("Aggregated confidence across the drafts — everything is held "
                        "for your approval.")
    # Honest fallback: an unmapped role is still narrated, not dropped.
    return f"{role.capitalize()}: {n} step(s) completed.{fail_note}"


def chat_mirror_turns(
    agent_runs: list[dict[str, Any]] | None,
) -> list[tuple[str, str, str | None]]:
    """Collapse a run's recorded ``agent_runs`` into client-readable chat turns —
    ONE turn per role, in first-appearance order, as ``(role, text, model)``.

    This is what the studio conversation shows for a run (tlv.3): the live drive
    proved that mirroring every per-lead agent_run verbatim floods the client
    transcript with internals (24x analyst rows, raw planner JSON, CellExecutionError
    text). The full per-step detail intentionally REMAINS on the run's ``agent_runs``
    (Runs tab / lineage); the conversation carries only the summary a studio owner
    should read. Pure + DB-free, so it is unit-testable."""
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for ar in agent_runs or []:
        if not isinstance(ar, dict):
            continue
        role = str(ar.get("role") or "").strip().lower() or "host"
        if role not in groups:
            groups[role] = []
            order.append(role)
        groups[role].append(ar)
    turns: list[tuple[str, str, str | None]] = []
    for role in order:
        runs = groups[role]
        model = next((str(r.get("model")) for r in runs if r.get("model")), None)
        turns.append((role, _mirror_text(role, runs), model))
    return turns


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


def _operator_turn_text(messages: list[dict[str, Any]] | None) -> str:
    """The operator turn to persist for ONE ``POST /studio/agui`` dispatch: the final
    message's text iff that final message is a USER turn, else ``''``.

    The approval resume re-POSTs the SAME thread with an assistant tool-call message
    appended — the operator said nothing new, so persisting the (stale) last user
    message again double-writes it (the live duplicate-operator-turn bug, tlv.3)."""
    msgs = [m for m in (messages or []) if isinstance(m, dict)]
    if not msgs or msgs[-1].get("role") != "user":
        return ""
    content = msgs[-1].get("content")
    return content if isinstance(content, str) else str(content or "")


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
    campaign_type: str | None = None,
    output_count: int | None = None,
    lead_count: int | None = None,
    offer: str | None = None,
    tone: str | None = None,
    artist: str | None = None,
    deep_research: bool | None = None,
    per_lead: bool | None = None,
    lead_source: str | None = None,
    leads: list[str] | None = None,
    use_conversation_history: bool | None = None,
    research_depth: str | None = None,
) -> Any:
    """Apply the operator's edits to the SHARED campaign plan, persist it, and
    snap the new state back to the UI. Pass ONLY the fields that changed.

    This is how a TYPED brief becomes the run's configuration — the same fields
    the interview panel sets. For a per-lead outreach run on specific people, set
    ``leads`` (their emails, or exact names when a lead has no email),
    ``lead_source='provided'``, ``per_lead=True``, ``channels``, ``lead_count``/
    ``output_count`` to the operator's exact number, and ``deep_research`` /
    ``research_depth='deep'`` when asked — THEN call `run_campaign`. Without
    these the run executes whatever stale plan the session last held."""
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
    if campaign_type is not None:
        plan.campaign_type = campaign_type
    if output_count is not None:
        plan.output_count = max(0, int(output_count))
    if lead_count is not None:
        plan.lead_count = max(0, int(lead_count))
    if offer is not None:
        plan.offer = offer
    if tone is not None:
        plan.tone = tone
    if artist is not None:
        plan.artist = artist
    if deep_research is not None:
        plan.deep_research = deep_research
    if per_lead is not None:
        plan.per_lead = per_lead
    if lead_source is not None:
        plan.lead_source = lead_source
    if leads is not None:
        plan.leads = [h.strip() for h in leads if (h or "").strip()]
    if use_conversation_history is not None:
        plan.use_conversation_history = use_conversation_history
    if research_depth is not None:
        plan.research_depth = research_depth

    await asyncio.to_thread(_persist_plan, ctx.deps.dsn, ctx.deps.session_id, plan)
    await asyncio.to_thread(
        _log_turn,
        ctx.deps.dsn,
        ctx.deps.session_id,
        "host",
        f"Updated the plan — goal: {plan.goal or 'not set yet'}; "
        f"audience: {plan.audience or 'not set yet'}; "
        f"channels: {', '.join(plan.channels) if plan.channels else 'not set yet'}.",
        HOST_AGUI_MODEL,
    )

    # Emit an AG-UI STATE_SNAPSHOT so the frontend's shared state updates. Imported
    # lazily so importing this module never requires the ag-ui protocol package.
    from ag_ui.core import EventType, StateSnapshotEvent

    return StateSnapshotEvent(type=EventType.STATE_SNAPSHOT, snapshot=plan.model_dump())


@studio_agent.tool
async def describe_brand_voice(ctx: RunContext[StudioDeps]) -> str:
    """Return the studio's REAL, currently-loaded brand voice so you can tell the
    operator EXACTLY what voice you write in — tone, structure, preferred + banned
    lexicon, and the approved-claims allow-list — resolved from the tenant pack
    (the same source the copywriter + critic cells write/judge in). Call this whenever
    the operator asks what brand voice you are using; never claim you don't know it.
    Honest: if the pack genuinely cannot resolve, say so and name the tenant."""

    def _resolve() -> tuple[str, tuple[str, ...], str]:
        from studio.customer_research import resolve_brand_voice

        # Resolve for THIS run's tenant only. We never fall back to the fixture default
        # here: surfacing another tenant's voice under this tenant's name would be a
        # fabrication. If it can't resolve, report the failure honestly and NAME the
        # tenant (r8: kill ladies8391 fixture bleed).
        tid = ctx.deps.tenant_id
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
        _log_turn,
        dsn,
        sid,
        "funnel_architect",
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
        _log_turn,
        dsn,
        sid,
        "copywriter",
        f"[copywriter] hook: {top.hook} | CTA: {top.call_to_action}",
        "anthropic:claude-haiku-4-5",
    )

    # 3) Critic — a real INDEPENDENT pass over the copy (never a staged debate)
    critique = await build_critic_cell().run(
        f"Asset to critique (instagram caption):\nHook: {top.hook}\n"
        f"Caption: {top.caption}\nCTA: {top.call_to_action}"
    )
    await asyncio.to_thread(
        _log_turn,
        dsn,
        sid,
        "critic",
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
    await asyncio.to_thread(_log_turn, dsn, sid, "jury", f"[jury] {verdict.output}", JURY_MODEL)

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
        brief += f"\nUploaded customer list: {cust['rows']} row(s)" + (
            f"; columns: {cols}" if cols else ""
        )
    # Carry uploaded brand / strategy notes into the run brief too. Bounded so a large
    # notes file can't blow the brief out.
    notes = plan.notes.strip()
    if notes:
        if len(notes) > 2000:
            notes = notes[:2000] + " …[truncated]"
        brief += f"\nBrand / strategy notes (operator-provided): {notes}"
    return brief


def _zero_draft_reason(summary: dict[str, Any]) -> str:
    """WHY a run staged nothing, read from the run's own recorded evidence — the
    failure summary (a required gate failed), else the output ledger's per-row skip
    reasons, else an honest 'no reason recorded' pointer to the Runs trace. Never
    invents a cause."""
    failures = summary.get("failure_summary") or []
    if failures:
        first = failures[0] if isinstance(failures[0], dict) else {}
        more = f" (+{len(failures) - 1} more)" if len(failures) > 1 else ""
        return (f"the {first.get('agent', 'required')} step failed: "
                f"{str(first.get('error', ''))[:160]}{more}")
    skipped = (summary.get("output_ledger") or {}).get("skipped") or []
    reasons = "; ".join(sorted({
        str(s.get("reason", "")).strip() for s in skipped
        if isinstance(s, dict) and s.get("reason")
    }))
    if reasons:
        return f"every lead was skipped ({reasons})"
    return ("no skip reason was recorded — the step-by-step trace in the Runs tab "
            "shows what each agent did")


def _campaign_id_from_run_id(run_id: str | None) -> str | None:
    """Recover the ``camp_...`` id embedded in a ``team-{campaign_id}-{hex}`` run id
    (mirrors the parse in ``_execute_provided_leads_sync``), or None when unavailable."""
    if not run_id:
        return None
    parts = run_id.split("-")
    return parts[1] if len(parts) >= 2 and parts[1].startswith("camp_") else None


def _summary_text(summary: dict[str, Any]) -> str:
    # Honest not-built (nmh.9): a channel with no pipeline yet returns its truthful
    # message, never a fabricated "ran the campaign / 0 drafts" line.
    if summary.get("run_status") == "not_built":
        return str(summary.get("message") or "That pipeline isn't built yet — nothing ran.")
    # Mid-run artwork pause (item 3): the run is WAITING on the operator's pick —
    # say so honestly (no drafts exist yet; nothing failed, nothing was sent).
    if summary.get("run_status") == "awaiting_selection":
        req = summary.get("selection_request") or {}
        n = len(req.get("options") or [])
        return (
            f"{req.get('question') or f'{n} artwork option(s) found.'} The run is "
            "paused before drafting until you pick one — nothing has been drafted "
            "or sent."
        )
    chans = ", ".join(summary.get("channels", [])) or "the selected channels"
    runs_note = (
        " You can watch each agent's step in the Runs tab."
        if summary.get("runs_row")
        else " (Per-agent traces are in this thread; the Runs-tab row was unavailable.)"
    )
    # A 0-draft run must explain itself in the host line (tlv.3): silence here read
    # as a broken product in the live drive. The reason comes from the run's own
    # recorded evidence — never invented.
    if not summary.get("n_queued", 0) and not summary.get("n_pending", 0):
        return (
            f"The '{summary.get('archetype_id')}' run finished without staging any "
            f"drafts — {_zero_draft_reason(summary)}.{runs_note} "
            "Want me to adjust the plan and try again?"
        )
    return (
        f"Ran the '{summary.get('archetype_id')}' campaign (run {summary.get('run_id')}). "
        f"The team produced {summary.get('n_queued', 0)} draft(s) across {chans} and staged "
        f"{summary.get('n_pending', 0)} action(s) PENDING approval — everything is HELD, "
        f"nothing was sent.{runs_note} Want me to refine a draft or stage one for approval?"
    )


def _use_provided_leads(plan: CampaignPlan) -> bool:
    """The hard compliance branch: True iff the operator chose to use ONLY their own
    leads (uploaded CSV / existing DB) rather than sourcing new ones from the web.

    DETERMINISTIC, not model-dependent: besides the explicit interview answer
    (``lead_source='provided'``), any plan state that can ONLY mean "my own
    people" selects this path — the operator NAMED leads, asked for per-lead
    messages, or asked the team to read their imported conversation threads.
    A real operator said "pick them from the imported conversation threads,
    use their real conversations" and the run still executed the generic
    win_back TEMPLATE (one niche researcher, no analyst, recipientless drafts)
    because the voice host had set every field EXCEPT lead_source."""
    from studio.interview import LEAD_SOURCE_PROVIDED

    if (plan.lead_source or "").strip().lower() == LEAD_SOURCE_PROVIDED:
        return True
    if [h for h in (plan.leads or []) if (h or "").strip()]:
        return True  # operator-picked people — never a template blast
    if plan.use_conversation_history is True:
        return True  # "use their real conversations" = per-lead over MY threads
    if plan.per_lead is True:
        return True  # one personalized message per lead = per-lead executor
    return False


def plan_campaign(plan: CampaignPlan, tenant_id: str, dsn: str | None) -> Any:
    """THE PLANNER step (plan-first, P1.5 blueprint #1). Build the executable
    :class:`~studio.campaign_blueprint.CampaignBlueprint` from the interview intent and
    write the RESOLVED assumed objection back onto the plan (so the progress-aware replan
    has a recorded assumption to test). Runs ONCE, in the DISPATCHER, BEFORE the lead-
    source branch — so ONE blueprint fronts BOTH the provided-leads path and the compose
    spine, never buried in a decorative compose node."""
    from studio.campaign_blueprint import build_blueprint, resolve_assumed_objection

    blueprint = build_blueprint(plan, tenant_id, dsn)
    assumed = resolve_assumed_objection(plan)
    if assumed and not (getattr(plan, "assumed_objection", "") or "").strip():
        plan.assumed_objection = assumed
    return blueprint


def _planner_run_output(blueprint: Any) -> dict[str, Any]:
    """The planner ``agent_run.output`` — carries the FULL blueprint (so the UI reads the
    plan from the durable agent_run, with no separate table) plus flattened highlights."""
    return {
        "targets": blueprint.targets.model_dump(),
        "per_channel_quota": blueprint.per_channel_quota,
        "offer_logic": [r.model_dump() for r in blueprint.offer_logic],
        "assumed_dominant_objection": blueprint.assumed_dominant_objection,
        "research_questions": blueprint.research_questions,
        "stop_conditions": blueprint.stop_conditions.model_dump(),
        "compliance_constraints": blueprint.compliance_constraints,
        "review_rules": blueprint.review_rules,
        "artist_shop_rules": blueprint.artist_shop_rules,
        "angle": blueprint.angle,
        "rationale": blueprint.planner_rationale,
        # The full plan lives here — the durable source of truth (no blueprint table).
        "blueprint": blueprint.model_dump(),
    }


def _record_planner_run(
    dsn: str | None, run_id: str, campaign_id: str, tenant_id: str, blueprint: Any
) -> None:
    """Record the planner as a real ``agent_run(role='planner')`` into ``run_id`` (used for
    the compose path; the provided-leads path records it INLINE as its first step so it
    orders before the per-lead rows). Best-effort — never breaks a real run."""
    try:
        import uuid as _uuid

        from team.store import TeamStore

        ts = TeamStore(dsn)
        ts.setup()
        ts.record_agent_run(
            id=f"ar_planner_{_uuid.uuid4().hex[:16]}",
            campaign_id=campaign_id,
            run_id=run_id,
            role="planner",
            model=blueprint.planner_model,
            input={"goal": blueprint.goal, "target_category": blueprint.targets.category},
            output=_planner_run_output(blueprint),
        )
        from studio import blueprint_store

        blueprint_store.upsert_blueprint(
            run_id,
            blueprint.model_dump(),
            campaign_id=campaign_id,
            tenant_id=tenant_id,
            planner_model=blueprint.planner_model,
            dsn=dsn,
        )
    except Exception:
        pass


def _execute_campaign_sync(
    plan: CampaignPlan,
    session_id: str,
    tenant_id: str,
    dsn: str | None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """SYNC: run the real traced Phase-A campaign for ``plan`` and persist its visible
    surfaces — mirror each per-role trace into the thread as a LABELED turn (so the
    operator can watch what each agent thought) and reflect the work into the shared
    plan. ``run_id`` lets the async endpoint poll the per-role ``agent_runs`` live.
    Returns the runner summary. NOTHING is sent (HELD/PENDING only).

    PLAN-FIRST: the PLANNER runs here, in the dispatcher, BEFORE the lead-source branch,
    so ONE blueprint fronts BOTH paths. Then it branches on the operator's LEAD-SOURCE
    choice: ``provided`` runs the per-lead compliance path (target ONLY the operator's
    leads, research each one); otherwise the web-sourcing Phase-A spine runs."""
    blueprint = plan_campaign(plan, tenant_id, dsn)

    # CHANNEL ROUTING (nmh.9 / spec §16): pick the workflow from the operator's REQUEST
    # — 'send emails' → email, 'create an Instagram post' → the IG spine, 'Facebook
    # campaign' / 'artist with attachments' → an HONEST "not built yet" (no fake run) —
    # instead of always running the email agents. Runs BEFORE the lead-source branch so
    # it fronts both the voice GO-gate and the /studio/run button.
    from studio.channel_router import Pipeline, not_built_summary, route_pipeline

    decision = route_pipeline(plan)

    # A channel with no real supervisor-invoked pipeline yet returns an honest not-built
    # response: nothing runs, zero agent_runs, no runs row — the DB/trace proves no fake
    # run happened. NEVER a fabricated email run dressed up as the requested channel.
    if not decision.built:
        campaign_id = _campaign_id_from_run_id(run_id)
        summary = not_built_summary(decision, run_id=run_id, campaign_id=campaign_id)
        try:
            _log_turn(dsn, session_id, "host", summary["message"], None)
        except Exception:
            pass
        return summary

    # Email + the operator's OWN uploaded leads → the per-lead outreach compliance path
    # (unchanged). Instagram deliberately does NOT take this branch — an IG post is a
    # posting campaign, not per-lead email outreach.
    if decision.pipeline == Pipeline.EMAIL and _use_provided_leads(plan):
        summary = _execute_provided_leads_sync(
            plan, session_id, tenant_id, dsn, run_id, blueprint=blueprint
        )
        summary.setdefault("routed_channel", decision.channel)
        summary["pipeline_built"] = True
        return summary

    from studio.campaign_runner import run_and_trace

    # Instagram pins the IG-first archetype so the traced run is genuinely the IG
    # workflow (archetype + channels prove it), not the email path. Email/default keeps
    # today's campaign_type-driven archetype selection.
    force_archetype = decision.archetype_id if decision.pipeline is Pipeline.INSTAGRAM else None

    brief = _brief_from_plan(plan)
    ig_artwork: dict[str, Any] | None = None
    ig_artwork_note: str | None = None
    if decision.pipeline is Pipeline.INSTAGRAM:
        # An IG post needs a run id UP FRONT (for the artwork pause + the channel-crew
        # trace rows) — mint one in the same camp/team format the launcher uses.
        if not run_id:
            campaign_id = f"camp_{uuid.uuid4().hex[:12]}"
            run_id = f"team-{campaign_id}-{uuid.uuid4().hex[:12]}"

        # ARTWORK GATE (item 3): every IG run attaches real artwork when the library
        # has it — surface the top picks and PAUSE for the operator's choice BEFORE
        # any drafting. An empty library proceeds with the honest note, never a pause.
        from studio.artwork_flow import (
            artwork_gate,
            awaiting_selection_summary,
            theme_terms_from_plan,
        )

        _gate_state, _gate_payload = artwork_gate(
            run_id,
            tenant_id,
            session_id,
            plan,
            artist=(plan.artist or "").strip() or None,
            theme_terms=theme_terms_from_plan(plan),
            dsn=dsn,
        )
        if _gate_state == "pause":
            try:
                _log_turn(
                    dsn, session_id, "host",
                    str(_gate_payload.get("question") or "Artwork pick needed."), None,
                )
            except Exception:
                pass
            return awaiting_selection_summary(
                run_id, _campaign_id_from_run_id(run_id), _gate_payload,
                channel=decision.channel,
            )
        if _gate_state == "selected":
            ig_artwork = _gate_payload
        else:
            ig_artwork_note = str(_gate_payload)

        # IG PIPELINE DEPTH (item 6): ground the brief in the artist's REAL memory
        # (profile + past campaigns + artwork tags) and live trend research, recorded
        # as channel-specific agent_runs so the IG crew is visibly different.
        try:
            from studio.ig_pipeline import build_ig_brief_block

            brief += build_ig_brief_block(
                plan,
                tenant_id,
                run_id=run_id,
                campaign_id=_campaign_id_from_run_id(run_id),
                artwork=ig_artwork,
                artwork_note=ig_artwork_note,
                dsn=dsn,
            )
        except Exception:
            pass  # grounding is additive; a hiccup never blocks the real run

    summary = run_and_trace(
        brief=brief,
        tenant_id=tenant_id,
        dsn=dsn,
        run_id=run_id,
        archetype_id=force_archetype,
        force_research=bool(plan.deep_research),
        output_count=plan.output_count or 0,
        campaign_type=plan.campaign_type or None,
        # The operator's answered channels constrain the archetype fan-out —
        # 'email channel, three drafts' must never emit SMS from the spec menu.
        plan_channels=list(plan.channels or []),
    )
    summary["routed_channel"] = decision.channel
    summary["pipeline_built"] = True
    if decision.pipeline is Pipeline.INSTAGRAM:
        summary["artwork"] = ig_artwork
        summary["artwork_note"] = ig_artwork_note
        if ig_artwork_note:
            summary.setdefault("step_notes", []).append(f"artwork: {ig_artwork_note}")
        # Post-staging: land artist + artwork + hashtags/cta on every post action's
        # context so the review UI / evidence shows them (item 6c). Best-effort.
        try:
            from studio.ig_pipeline import enrich_post_actions

            enriched = enrich_post_actions(
                summary.get("run_id") or run_id,
                tenant_id,
                artist=(plan.artist or "").strip() or None,
                artwork=ig_artwork,
                dsn=dsn,
            )
            if enriched:
                summary.setdefault("step_notes", []).append(
                    f"attached artist/artwork/hashtags context to {enriched} staged post(s)"
                )
        except Exception:
            pass
    # Record the planner into the SAME compose run so the plan step is not decorative for
    # the compose spine either (the provided path records it inline as its first step).
    if summary.get("run_id"):
        _record_planner_run(
            dsn,
            str(summary["run_id"]),
            str(summary.get("campaign_id") or ""),
            tenant_id,
            blueprint,
        )
    # ONE collapsed, client-readable turn per role (tlv.3) — never the raw per-lead
    # output_summary spam; the full detail stays on agent_runs (Runs tab).
    for role, text, model in chat_mirror_turns(summary.get("agent_runs", [])):
        role = role if role in VALID_ROLES else "host"
        _log_turn(dsn, session_id, role, text, model)
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
    plan: CampaignPlan,
    session_id: str,
    tenant_id: str,
    dsn: str | None,
    run_id: str | None = None,
    blueprint: Any = None,
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
    import json as _json
    import uuid as _uuid

    from actions.store import ensure_schema, record_pending_action
    from memory import MemoryStore
    from studio.adapters.message_source import DbConversationSource
    from studio.dossier import build_dossier
    from studio.skill_select import select_skill
    from studio.campaign_runner import (
        FAILCLOSED_REQUIRED_ROLES,
        _materialize_runs_row,
        _summarize_output,
        campaign_run_status,
        required_step_failures,
    )
    from studio.customer_research import (
        _research_enabled,
        build_outreach_draft,
        churn_risk_leads,
        contactable_leads,
        conversation_leads,
        lookup_leads,
        research_studio,
    )
    from cells.identity_guard import foreign_identity_violations
    from cells.offer_guard import offer_violations
    from cells.personalization_guard import facts_view as personalization_facts_view
    from cells.personalization_guard import personalization_violations
    from studio.offers import as_substantiated, get_offers, select_offer, substantiate
    from studio.psych_profile import analyze_customer, psych_llm_model

    if not run_id:
        campaign_id = f"camp_{_uuid.uuid4().hex[:12]}"
        run_id = f"team-{campaign_id}-{_uuid.uuid4().hex[:12]}"
    else:
        parts = run_id.split("-")
        campaign_id = (
            parts[1]
            if len(parts) >= 2 and parts[1].startswith("camp_")
            else f"camp_{_uuid.uuid4().hex[:12]}"
        )

    store = MemoryStore(dsn=dsn)
    store.ensure_schema()
    ensure_schema(dsn)
    from team.store import TeamStore

    ts = TeamStore(dsn)
    ts.setup()

    # PLAN-FIRST: the executable blueprint — built by the dispatcher (``plan_campaign``)
    # BEFORE the branch and passed in, or built here on a direct call. It BOUNDS the
    # fan-out and GATES offer selection below, and is recorded as the FIRST agent_run.
    if blueprint is None:
        blueprint = plan_campaign(plan, tenant_id, dsn)
    blueprint.run_id = run_id
    blueprint.campaign_id = campaign_id
    # HARD fan-out cap (P1.5 blueprint #2): never run analyst+draft+critic for more than
    # the planned quota, ceilinged at a hard cap — a 5000-row uploaded CSV stages AT MOST
    # this many actions, never 5000×(analyst+draft+critic).
    #
    # CAP DECOUPLE (nmh.11): the compose spine's ``_OUTPUT_HARD_CAP`` (=12) bounds a
    # PARALLEL LangGraph Send fan-out (all draft workers in one superstep), so raising it
    # there would multiply concurrent LLM calls. This provided/cohort executor is a
    # SEQUENTIAL per-lead loop, so it gets its OWN, larger ceiling — the operator's N
    # (10/25/30/…) is a pure input and must reach N from the full customers table, never
    # silently clipped to 12. Env-overridable; still bounded so an absurd plan can't run
    # away. compose's constant is intentionally left untouched.
    _COHORT_HARD_CAP = int(os.environ.get("ENGINE_COHORT_HARD_CAP", "1000"))

    effective_cap = min(
        blueprint.stop_conditions.total_quota or _COHORT_HARD_CAP, _COHORT_HARD_CAP
    )

    # 1) Resolve ONLY the operator's leads — uploaded CSV ids first, else DB cohort. The
    # cust-id list is capped to ``effective_cap`` BEFORE the DB lookup so a huge CSV never
    # fans out; the cohort limit is likewise bounded by the cap.
    # OUTPUT-COUNT RECONCILIATION (P2-D, 65w.8): every requested row is accounted for as
    # either a staged draft or a SKIP with a concrete row-level reason — no silent
    # undercount. ``skipped`` accrues {row, lead, reason}; ``expected`` is what the operator
    # asked for; the ledger is reconciled + surfaced at the end.
    skipped: list[dict[str, Any]] = []
    requested_ids = list((plan.customers or {}).get("customer_ids") or [])
    cust_ids = requested_ids
    # 0) Operator-PICKED leads (plan.leads: emails / exact names chosen in chat,
    # e.g. from `list_conversation_leads`) outrank every other source: the operator
    # said "these three", so the full per-lead team loop runs on exactly those —
    # never a cohort that merely overlaps them. Same reconciliation contract as the
    # CSV path: every handle is a staged draft or a counted skip.
    picked_handles = [h.strip() for h in (plan.leads or []) if (h or "").strip()]
    if picked_handles:
        n_requested = len(picked_handles)
        expected = int(n_requested)
        for idx, h in enumerate(picked_handles[effective_cap:], start=effective_cap + 1):
            skipped.append(
                {"row": idx, "lead": h, "reason": f"beyond output cap of {effective_cap}"}
            )
        picked_handles = picked_handles[:effective_cap]
        leads = lookup_leads(
            tenant_id,
            [{"email": h} if "@" in h else {"name": h} for h in picked_handles],
            dsn=dsn,
            memory_store=store,
        )
        resolved_keys = {
            (f.get("email") or "").lower() for f in leads
        } | {(f.get("name") or "").lower() for f in leads}
        for idx, h in enumerate(picked_handles, start=1):
            if h.lower() not in resolved_keys:
                skipped.append(
                    {
                        "row": idx,
                        "lead": h,
                        "reason": "not found in database (no customer matched this email/name)",
                    }
                )
        source_note = (
            f"operator-picked leads ({len(leads)} of {n_requested} resolved in DB)"
        )
    elif cust_ids:
        n_requested = len(cust_ids)
        # Provided path: the operator handed us N leads and expects one draft per lead
        # (AC: "10 leads = 10 drafts"). ``output_count`` is a cohort-sizing knob, not the
        # provided-row count, so expected == the rows supplied — every row reconciles.
        expected = int(n_requested)
        # Rows beyond the hard cap are SKIPPED with a reason (not silently dropped).
        for idx, cid in enumerate(cust_ids[effective_cap:], start=effective_cap + 1):
            skipped.append(
                {"row": idx, "lead": cid, "reason": f"beyond output cap of {effective_cap}"}
            )
        cust_ids = cust_ids[:effective_cap]
        leads = lookup_leads(
            tenant_id, [{"customer_id": i} for i in cust_ids], dsn=dsn, memory_store=store
        )
        # Requested rows that did not resolve to a real customer are SKIPPED with a reason.
        resolved_ids = {f.get("customer_id") for f in leads}
        for idx, cid in enumerate(cust_ids, start=1):
            if cid not in resolved_ids:
                skipped.append(
                    {
                        "row": idx,
                        "lead": cid,
                        "reason": "not found in database (row did not match a customer)",
                    }
                )
        capped_note = f", capped to {effective_cap}" if n_requested > effective_cap else ""
        source_note = (
            f"uploaded CSV ({len(leads)} of {n_requested} rows resolved in DB{capped_note})"
        )
    else:
        limit = min(plan.lead_count or plan.output_count or 10, effective_cap)
        # DRAFT-COUNT EXACTNESS (nmh.1, spec §14): the operator asked for ``limit``
        # drafts and must get EXACTLY that many (one per valid contact) — never a
        # self-chosen smaller number. Build the cohort to ``limit`` in priority order,
        # de-duplicated by customer_id: the tenant's WARM leads first (best signal —
        # per-lead psych off their real chat), then the churn / win-back cohort, then
        # ANY remaining contactable customer. Only a tenant with genuinely fewer than
        # ``limit`` valid contacts yields fewer (reconciled below with a counted skip).
        leads = []
        picked: set[str] = set()

        def _fill_from(source_leads: list[dict[str, Any]]) -> int:
            n0 = len(leads)
            for lead in source_leads:
                if len(leads) >= limit:
                    break
                cid = lead.get("customer_id")
                if cid and cid not in picked:
                    picked.add(cid)
                    leads.append(lead)
            return len(leads) - n0

        n_warm = _fill_from(
            conversation_leads(tenant_id, limit=limit, dsn=dsn, memory_store=store)
        )
        n_churn = 0
        if len(leads) < limit:
            n_churn = _fill_from(
                churn_risk_leads(tenant_id, limit=limit, dsn=dsn, memory_store=store)
            )
        n_fill = 0
        if len(leads) < limit:
            n_fill = _fill_from(contactable_leads(
                tenant_id, limit=limit, exclude_ids=list(picked), dsn=dsn,
                memory_store=store,
            ))
        _parts = [
            f"{n_warm} with conversation history" if n_warm else "",
            f"{n_churn} win-back / lapsing" if n_churn else "",
            f"{n_fill} other contactable" if n_fill else "",
        ]
        _parts = [p for p in _parts if p]
        source_note = (
            f"your database cohort ({', '.join(_parts)})" if _parts
            else "your database cohort (no contactable customers found)"
        )

    # Cohort path: expected = the operator's count. Any shortfall is reconciled with a
    # counted skip row (``count`` accounts for every missing slot) so the ledger balances
    # (requested = created + accounted-shortfall), never a silent undercount. The reason
    # is HONEST about the cause: contact-supply exhaustion vs the output hard cap — never
    # "only N contactable" when it was actually the cap that clipped the run.
    # (Skipped for the picked-leads and CSV paths — their branches already reconciled
    # every requested handle/row, and ``limit`` only exists on the cohort path.)
    if not requested_ids and not picked_handles:
        expected = int(plan.output_count or plan.lead_count or len(leads))
        # ``limit`` was min(requested, effective_cap): a request above the cap is clipped
        # by the cap, not by contact supply.
        capped_target = limit
        if len(leads) < capped_target:
            # Genuine contact exhaustion within the cap: fewer valid contacts than we
            # could have drafted for.
            shortfall = capped_target - len(leads)
            skipped.append({
                "row": None, "lead": "cohort", "count": shortfall,
                "reason": (f"tenant has only {len(leads)} contactable customer(s); "
                           f"{shortfall} short of requested {expected}"),
            })
        if capped_target < expected:
            # The requested count exceeds the output hard cap — the remainder is clipped
            # by the cap (roadmap #1 territory), surfaced honestly rather than hidden.
            cap_short = expected - capped_target
            skipped.append({
                "row": None, "lead": "cohort", "count": cap_short,
                "reason": (f"{cap_short} beyond the output cap of {effective_cap} "
                           f"(requested {expected})"),
            })

    goal = plan.goal or "win back lapsed clients"
    # research_depth == "deep" (the interview answer) opts research in too (spec §7).
    deep = _research_enabled(plan.deep_research, plan.research_depth or None)
    agent_runs: list[dict[str, Any]] = []
    pending: list[str] = []

    # Real substantiated offers for the tenant (from the offers doc); [] when none, so a
    # discount is referenced ONLY when a real offer exists — never invented. The lead's
    # conversation (the psych analyst's primary evidence) comes from the DB conversation
    # store via the message-source adapter. The offer_guard view backs the per-draft
    # anti-fabrication check below (65w.14: seed offers substantiate nothing there).
    offers = get_offers(tenant_id, dsn=dsn)
    _guard_offers = as_substantiated(offers)
    conv_source = DbConversationSource(tenant_id, dsn=dsn)

    def _rec(
        role: str, model: str, inp: dict[str, Any], out: dict[str, Any],
        *, id_: str | None = None,
    ) -> None:
        # ``id_`` lets ONE-SHOT roles (planner/strategist/jury) use a DETERMINISTIC id:
        # a run resumed after the artwork-selection pause re-records them as a no-op
        # (record_agent_run is ON CONFLICT DO NOTHING) instead of duplicating steps.
        ts.record_agent_run(
            id=id_ or f"ar_{_uuid.uuid4().hex[:16]}",
            campaign_id=campaign_id,
            run_id=run_id,
            role=role,
            model=model,
            input=inp,
            output=out,
        )
        agent_runs.append(
            {
                "role": role,
                "model": model,
                "input": inp,
                "output": out,
                "output_summary": _summarize_output(role, out),
            }
        )

    def _cell_model(cell: Any) -> str:
        m = getattr(cell, "model", None)
        return m if isinstance(m, str) else str(m)

    # 1a) PLANNER — plan-first (P1.5 blueprint #1). The blueprint is built by the DISPATCHER
    # (``plan_campaign``) BEFORE the lead-source branch and passed in; on a direct/standalone
    # call it is built here. It is recorded as the FIRST agent_run (role='planner', with the
    # FULL blueprint in the output) so the war-room shows the plan step first, AND persisted
    # to its dedicated ``campaign_blueprints`` row (the authored plan — distinct from the
    # progress BOARD, which stays derived/on-demand with no table). The per-lead loop then
    # executes AGAINST it (quota caps the fan-out, offer_logic gates offers).
    from studio import blueprint_store
    from studio.campaign_blueprint import offer_rule_for
    from studio.progress_board import (
        board_for_run,
        maybe_replan,
        replan_event_id,
    )

    _rec(
        "planner",
        blueprint.planner_model,
        {
            "goal": blueprint.goal,
            "target_category": blueprint.targets.category,
            "scope": blueprint.targets.scope,
            "channels": list(plan.channels or []),
        },
        _planner_run_output(blueprint),
        id_=f"ar_planner_{hashlib.sha1(str(run_id).encode()).hexdigest()[:16]}",
    )

    def _persist_blueprint() -> None:
        try:
            blueprint_store.upsert_blueprint(
                run_id,
                blueprint.model_dump(),
                campaign_id=campaign_id,
                tenant_id=tenant_id,
                session_id=session_id,
                planner_model=blueprint.planner_model,
                dsn=dsn,
            )
        except Exception:
            pass  # the blueprint row is a read convenience; never break a real run

    _persist_blueprint()

    # 1b) STRATEGIST runs ONCE for the campaign — the REAL strategy cell sets the angle
    # the per-lead drafts lead with, recorded as a real agent_run (so the Strategist
    # lane reads `done` with real lineage, not skipped). A cell hiccup records an honest
    # `failed` strategist run and the run continues on the base goal — never a crash, and
    # never a fabricated angle.
    from cells.strategy import build_strategy_cell, build_strategy_prompt
    from config.loader import describe_tenant

    # MODEL-FAILURE CIRCUIT BREAKER state (see MODEL_FAILURE_BREAKER_THRESHOLD): when
    # the strategist ALSO failed and N CONSECUTIVE leads hit a REAL model/HTTP error
    # (critic and/or draft cell), the loop stops honestly instead of grinding the whole
    # cohort staging junk fallback drafts. Missing-key deterministic fallbacks (cells
    # never attempt a call) never count — only repeated real model errors do.
    _strategist_failed = False
    _model_fail_streak = 0
    _last_model_error: str | None = None
    _breaker_tripped = False

    # Build the strategist cell OUTSIDE the try so BOTH the success and the honest-failed
    # run record its REAL model (never a hardcoded literal that could drift from the pin).
    strat_cell = build_strategy_cell()
    strat_model = _cell_model(strat_cell)
    campaign_angle: str | None = None
    try:
        strategy = strat_cell.run_sync(
            build_strategy_prompt(describe_tenant(tenant_id), _brief_from_plan(plan))
        )
        campaign_angle = (strategy.target_angle or "").strip() or None
        _rec(
            "strategist",
            strat_model,
            {"goal": goal, "n_leads": len(leads), "lead_source": "provided"},
            strategy.model_dump(),
            id_=f"ar_strategist_{hashlib.sha1(str(run_id).encode()).hexdigest()[:16]}",
        )
    except Exception as exc:  # honest failed run, never a fabricated strategy
        _strategist_failed = True
        _rec(
            "strategist",
            strat_model,
            {"goal": goal, "lead_source": "provided"},
            {"status": "failed", "error": f"{type(exc).__name__}: {exc}"},
            id_=f"ar_strategist_{hashlib.sha1(str(run_id).encode()).hexdigest()[:16]}",
        )

    # ARTWORK ATTACH GATE (engine-core item 3, spec §9/10/22) — AFTER strategy, BEFORE
    # any drafting. When the operator asked for artwork on this cohort, surface the TOP
    # matching pieces and PAUSE for their pick; a durable prior pick resumes straight
    # through (the planner/strategist above re-recorded as no-ops via deterministic
    # ids). An empty library NEVER pauses — the run proceeds with the honest note.
    selected_artwork: dict[str, Any] | None = None
    artwork_note: str | None = None
    if bool(getattr(plan, "attach_artwork", False)):
        from studio.artwork_flow import (
            artwork_gate,
            awaiting_selection_summary,
            theme_terms_from_plan,
        )

        _gate_state, _gate_payload = artwork_gate(
            run_id,
            tenant_id,
            session_id,
            plan,
            artist=(plan.artist or "").strip() or None,
            theme_terms=theme_terms_from_plan(
                plan, extra=[campaign_angle] if campaign_angle else None
            ),
            dsn=dsn,
        )
        if _gate_state == "pause":
            try:
                _log_turn(
                    dsn, session_id, "host",
                    str(_gate_payload.get("question") or "Artwork pick needed."), None,
                )
            except Exception:
                pass
            return awaiting_selection_summary(
                run_id, campaign_id, _gate_payload,
                channel="email", agent_runs=agent_runs,
            )
        if _gate_state == "selected":
            selected_artwork = _gate_payload
        else:  # "none" — honest note, proceed without artwork
            artwork_note = str(_gate_payload)

    # The real critic cell, built once and run PER draft below (independent pass).
    from cells.critic import build_critic_cell

    critic_cell = build_critic_cell()

    # The per-lead draft leads with the strategist's real angle when one was produced,
    # so the strategist is load-bearing (the angle flows into the copy), not decoration.
    draft_goal = (
        goal if not campaign_angle else f"{goal}. Lead with this campaign angle: {campaign_angle}"
    )

    # DURABLE STEP LEDGER (fr1.2 / OPS-2): make the per-lead loop crash-safe. Active
    # when ENGINE_DATABASE_URL is set (the same activation seam as the checkpointer);
    # a laptop reboot mid-campaign then resumes at the exact lead being drafted — an
    # already-staged lead is a ledger no-op on restart (its expensive re-draft is
    # skipped), and the staged action row's idempotency_key (``run_id:cust_id``) owns
    # effect-level exactly-once regardless. Best-effort: a ledger-setup failure never
    # breaks the run (``_durable`` stays None -> the pre-fr1.2 behavior).
    _durable = None
    if run_id:
        from harness.config import get_settings

        if get_settings().database_url:
            try:
                from studio.durable_run import DurableRun

                _durable = DurableRun(run_id, tenant_id, dsn=dsn)
                _durable.ensure_schema()
            except Exception:
                _durable = None

    # DRAFT-COUNT EXACTNESS under guard skips (nmh.1 follow-through, spec §14): a draft
    # killed by a post-generation guard (foreign-identity / offer / personalization)
    # frees a quota slot. On the DB-COHORT path we REFILL that slot from the next valid
    # contactable customer (bounded at 2x quota), so "ask 3, get 3" holds even when a
    # guard rejects a draft — never a silent under-delivery. The uploaded-CSV path NEVER
    # substitutes leads the operator did not provide: there a skip stays a skip, with
    # its concrete reason in the ledger (spec: "Requested 25, valid 18 -> Created 18").
    # Picked-leads and CSV paths pin the quota to the operator's explicit rows and
    # never substitute; only the DB-cohort path has a ``limit`` and may refill.
    _operator_rows = bool(requested_ids or picked_handles)
    _quota = (
        min(int(expected), effective_cap) if _operator_rows
        else min(limit, effective_cap)
    )
    _refill_cap = _quota * 2
    _refilled = 0

    def _lead_stream():
        nonlocal _refilled
        for _f in leads:
            yield _f
        if _operator_rows:
            return  # operator-provided rows only — no substitution
        while len(pending) < _quota and _refilled < _refill_cap:
            _batch = contactable_leads(
                tenant_id,
                limit=max(_quota - len(pending), 1),
                exclude_ids=list(picked),
                dsn=dsn,
                memory_store=store,
            )
            _fresh = [
                f for f in _batch
                if f.get("customer_id") and f["customer_id"] not in picked
            ]
            if not _fresh:
                return  # contact supply exhausted — the skip ledger stays the honest record
            for _f in _fresh:
                if len(pending) >= _quota or _refilled >= _refill_cap:
                    return
                picked.add(_f["customer_id"])
                _refilled += 1
                yield _f
        # SUPERVISOR REDO QUEUE: re-process leads the supervisor ordered redone —
        # only ones this run has not already handled (a staged lead's re-draft is
        # the operator's reject-then-redo flow, never a silent duplicate).
        while _sup_redo_ids:
            _cid = _sup_redo_ids.pop()
            if _cid in _sup_processed_ids:
                continue
            for _f in lookup_leads(
                tenant_id, [{"customer_id": _cid}], dsn=dsn, memory_store=store
            ):
                yield _f

    # SUPERVISOR FULL-DUPLEX (spec: the supervisor orchestrates, not just watches).
    # At every safe boundary (before each lead) the executor consumes any PENDING
    # run_directives: abort/pause stop the fan-out honestly, set_angle/guide_copy
    # redirect subsequent drafts, set_offer switches to another SUBSTANTIATED offer,
    # skip_lead drops a lead with a ledger reason. Every application lands as a
    # role='supervisor' agent_run, so the intervention is visible in the live panel.
    from studio.supervisor_control import apply_directives as _apply_directives
    from studio.supervisor_control import check_plan_conformance as _check_conformance

    _sup_guidance: list[str] = []
    _sup_skip_ids: set[str] = set()
    _sup_redo_ids: set[str] = set()
    _sup_processed_ids: set[str] = set()
    _conf_fired: set[str] = set()
    _sup_offer_code: str | None = None
    _sup_stopped: str | None = None
    # The leads the run ACTUALLY selected/processed (the finalized cohort) — the real
    # facts rows the cohort-claim conformance note compares the plan's claims against.
    _cohort_seen: list[dict[str, Any]] = []

    # SIGN-OFF IDENTITY GATE (truth-gap fix: a draft signed 'Cheers, Keebs' for a lead
    # with NO Keebs link). An artist may front a draft ONLY when the operator
    # EXPLICITLY set plan.artist; otherwise only the lead's own recorded artist
    # affinity (customers.artist) may — a lead with neither signs as the studio.
    _plan_artist = (plan.artist or "").strip() or None

    def _consume_directives() -> None:
        nonlocal campaign_angle, draft_goal, _sup_offer_code, _sup_stopped
        try:
            changes = _apply_directives(
                run_id, tenant_id, dsn=dsn,
                record_agent_run=lambda **kw: _rec(
                    kw["role"], kw["model"], kw["input"], kw["output"]
                ),
            )
        except Exception:
            return  # steering is additive; a directive-store hiccup never kills the run
        if changes["angle"]:
            campaign_angle = changes["angle"]
            draft_goal = f"{goal}. Lead with this campaign angle: {campaign_angle}"
        if changes["guidance"]:
            _sup_guidance.extend(changes["guidance"])
            draft_goal = draft_goal + " " + " ".join(
                f"Operator guidance: {g}." for g in changes["guidance"]
            )
        if changes["offer_code"]:
            _sup_offer_code = changes["offer_code"]
        _sup_skip_ids.update(changes["skip_customer_ids"])
        _sup_redo_ids.update(changes.get("redo_customer_ids") or ())
        if changes["abort"]:
            _sup_stopped = "aborted by supervisor directive"
        elif changes["pause"]:
            _sup_stopped = "paused by supervisor directive (re-run resumes; staged leads replay-skip)"

    # 2) Per-lead: real DB history + research ABOUT this lead + brand-voiced draft.
    # The blueprint's stop_conditions + the hard cap bound the fan-out: draft at most
    # ``effective_cap`` leads. This makes the plan load-bearing — the executor stops when
    # the plan says stop, never an unbounded 5000× fan-out.
    for facts in _lead_stream():
        if len(pending) >= effective_cap:
            break  # stop_condition: per-run quota / hard cap met
        _consume_directives()
        # PLAN CONFORMANCE (operator order: keep steering until the run matches the
        # plan). Deterministic, evidence-only check over the steps recorded SO FAR;
        # each new finding lands as a visible supervisor step and its correction is
        # injected into every subsequent draft prompt.
        for _f in _check_conformance(plan, agent_runs, fired_rules=_conf_fired):
            _rec(
                "supervisor",
                "conformance:auto",
                {"rule": _f["rule"]},
                {"finding": _f["detail"], "correction": _f["correction"], "auto_enforced": True},
            )
            _sup_guidance.append(_f["correction"])
            draft_goal = draft_goal + f" Supervisor correction: {_f['correction']}"
        if _sup_stopped:
            skipped.append({"row": None, "lead": "run", "reason": _sup_stopped})
            break
        cust_id = facts["customer_id"]
        _sup_processed_ids.add(cust_id)
        if all(f.get("customer_id") != cust_id for f in _cohort_seen):
            _cohort_seen.append(facts)
        if cust_id in _sup_skip_ids:
            skipped.append(
                {"row": None, "lead": facts.get("name") or cust_id,
                 "reason": "skipped by supervisor directive"}
            )
            continue
        # Replay-skip (fr1.2): this lead was fully staged in a prior (crashed) drive.
        # Skip its re-draft; re-attach its already-staged action id to the payload so
        # the resumed run's count is honest. The action row itself already exists
        # (idempotency_key), so nothing re-fires.
        _step_key = f"{run_id}:{cust_id}:stage"
        if _durable is not None and _durable.has_run_step(_step_key):
            _prior = _durable.prior_result(_step_key) or {}
            _prior_aid = _prior.get("action_id") if isinstance(_prior, dict) else None
            if _prior_aid:
                pending.append(_prior_aid)
            continue
        research = research_studio(facts, enabled=deep)  # real Firecrawl about THIS studio
        # PER-LEAD PUBLIC ENRICHMENT (deep research on): the cited public-web read
        # of THIS lead (their business/professional/social presence — every stored
        # fact carries its source URL; sensitive traits are suppressed and counted;
        # zero facts is an honest miss, never invented). The enrichment memory it
        # writes flows into the dossier/draft prompt via enrichment_prompt_lines,
        # so the copywriter personalizes on evidence, not vibes. Best-effort: a
        # failure records honestly and the draft proceeds on DB facts alone.
        public_enrichment: dict[str, Any] | None = None
        if deep:
            try:
                from studio.lead_enrichment import enrich_lead

                _enr = enrich_lead(tenant_id, cust_id, dsn=dsn)
                public_enrichment = {
                    "found": len(_enr.get("found") or []),
                    "suppressed": int(_enr.get("suppressed") or 0),
                    "misses": len(_enr.get("misses") or []),
                    "memory_id": _enr.get("memory_id"),
                    "urls": [
                        f.get("url") for f in (_enr.get("found") or []) if f.get("url")
                    ][:5],
                }
            except Exception as exc:  # noqa: BLE001 — honest failure, never fatal
                public_enrichment = {
                    "found": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
        th = facts.get("tattoo_history", []) or []
        traits = facts.get("persona_traits", {}) or {}
        sources = [
            {
                "url": r.get("url"),
                "title": r.get("title"),
                "snippet": r.get("snippet"),
                "source_type": r.get("source_type"),
                "customer_id": r.get("customer_id") or cust_id,
            }
            for r in research
            if r.get("url")
        ][:5]
        _rec(
            "researcher",
            "firecrawl+customer_db",
            {"customer_id": cust_id, "name": facts.get("name")},
            {
                "cited": len(sources),
                "sources": sources,
                "lead": facts.get("name"),
                "customer_id": cust_id,
                "public_enrichment": public_enrichment,
                "db_history": {
                    "city": facts.get("city"),
                    "past_tattoos": len(th),
                    "interests": facts.get("interests", []),
                    "lifecycle": traits.get("lifecycle_stage"),
                    "win_back_candidate": traits.get("win_back_candidate"),
                    "prior_memories": len(facts.get("memories", []) or []),
                },
                "degraded": deep and len(sources) == 0,
            },
        )

        # ANALYST — the deep, evidence-grounded psychology read runs PER LEAD before the
        # draft, recorded as a real `analyst` agent_run so its lane reads done/failed
        # honestly. It grounds every read in this lead's real conversation/facts; a cell
        # hiccup records an honest failed run and the draft proceeds on the base facts.
        profile = None
        objection_val = ""
        interest_hint = (facts.get("interests") or [None])[0]
        try:
            thread = conv_source.thread_for(cust_id)
        except Exception:
            thread = None  # not-connected/stub source -> honest no conversation
        try:
            profile = analyze_customer(
                facts,
                thread,
                known_artists=[facts["artist"]] if facts.get("artist") else None,
            )
            po = profile.primary_objection
            objection_val = po.value if po.signal in ("stated", "inferred") else ""
            _rec(
                "analyst",
                (psych_llm_model() if profile.source.endswith("llm") else "grounded_rules"),
                {
                    "customer_id": cust_id,
                    "name": facts.get("name"),
                    "had_conversation": profile.had_conversation,
                },
                {
                    "umbrella_category": profile.umbrella_category.value,
                    "primary_objection": (
                        po.value if po.signal != "insufficient-signal" else "none-found"
                    ),
                    "objection_signal": po.signal,
                    "objection_evidence": po.evidence,
                    "readiness_stage": profile.readiness_stage.value,
                    "where_customer_sits": profile.where_customer_sits,
                    "best_reengagement_angle": profile.best_reengagement_angle,
                    "grounded_fields": profile.grounded_fields,
                    "insufficient_fields": profile.insufficient_fields,
                },
            )
        except Exception as exc:  # honest failed analyst run; never a fabricated profile
            profile = None
            _rec(
                "analyst",
                "grounded_rules",
                {"customer_id": cust_id},
                {"status": "failed", "error": f"{type(exc).__name__}: {exc}"},
            )

        # Offer selection DRIVEN BY the blueprint's offer_logic: the plan decides whether
        # an offer is PERMITTED for this objection (each rule was grounded in the real
        # offers doc at plan time). Only when the rule carries a real code do we
        # interest-match the concrete offer and pass it through the SUBSTANTIATION GATE.
        # No rule / no real offer -> the draft references NO discount, never an invented
        # one. This makes the blueprint load-bearing (not a decorative artifact).
        rule = offer_rule_for(blueprint, objection_val)
        chosen_offer = None
        if _sup_offer_code:
            # Supervisor directive: this run's offer was explicitly switched — the
            # substantiation gate still decides (an unknown code yields None, never
            # an invented discount).
            chosen_offer = substantiate(offers, _sup_offer_code)
        elif rule is not None and rule.offer_code:
            chosen_offer = select_offer(offers, objection=objection_val, interest=interest_hint)
            if chosen_offer is not None:
                chosen_offer = substantiate(offers, chosen_offer.code)

        _row = (requested_ids.index(cust_id) + 1) if cust_id in requested_ids else None
        # NO-CONTACT skip (P2-D): a truly unreachable / empty row — no email, phone, social
        # handle, OR even a name — cannot be drafted for, so skip it with a concrete reason
        # rather than staging an undeliverable draft. (A lead with a name but no email still
        # drafts: the existing path downgrades the channel and targets the handle/name — that
        # is NOT an undercount, so it is not skipped here.)
        if not (
            facts.get("email") or facts.get("phone") or facts.get("ig_handle") or facts.get("name")
        ):
            skipped.append(
                {
                    "row": _row,
                    "lead": cust_id,
                    "reason": "no contact method (no email, phone, handle, or name)",
                }
            )
            continue

        # SIGN-OFF IDENTITY: which artist (if any) may front THIS draft — the
        # operator's explicit plan.artist, else the lead's own recorded artist
        # affinity. Neither -> None -> the copywriter prompt hard-forbids signing
        # as / writing in the voice of any individual artist (studio voice only).
        _artist_voice = _plan_artist or (str(facts.get("artist") or "").strip() or None)
        try:
            draft = build_outreach_draft(
                facts,
                goal=draft_goal,
                tenant_id=tenant_id,
                plan_channels=plan.channels or None,
                deep_research=plan.deep_research,
                research_depth=plan.research_depth or None,
                research=research,
                profile=profile,
                offer=chosen_offer,
                artist_voice=_artist_voice,
            )
        except Exception as exc:  # honest per-row skip; never a crash or a fake draft
            if _is_model_error(exc):
                _model_fail_streak += 1
                _last_model_error = f"{type(exc).__name__}: {exc}"
            else:
                _model_fail_streak = 0
            skipped.append(
                {
                    "row": _row,
                    "lead": facts.get("name") or cust_id,
                    "reason": f"draft generation failed: {type(exc).__name__}",
                }
            )
            if _strategist_failed and _model_fail_streak >= MODEL_FAILURE_BREAKER_THRESHOLD:
                _breaker_tripped = True
                break
            continue

        # FOREIGN-IDENTITY GATE (wwy.7 r8, the smoking gun): a draft staged for this
        # tenant must never carry ANOTHER tenant's identity ("it's Rae from Ladies
        # First" on skindesign customers). Deterministic post-generation net — skips
        # with the concrete foreign tenant named.
        _id_viol = foreign_identity_violations(
            f"{draft.get('subject') or ''}\n{draft.get('draft') or ''}", tenant_id)
        if _id_viol:
            skipped.append({"row": _row, "lead": facts.get("name") or cust_id,
                            "reason": _id_viol[0]})
            continue

        # OFFER ANTI-FABRICATION (65w.14, the ARTLOVER audit): every offer/discount
        # token in the built copy must trace to a REAL operator-provided offer — seed
        # offers substantiate nothing. A violating draft is skipped with the concrete
        # reason; it never reaches the pending queue.
        _offer_viol = offer_violations(
            f"{draft.get('subject') or ''}\n{draft.get('draft') or ''}", _guard_offers
        )
        if _offer_viol:
            skipped.append(
                {
                    "row": _row,
                    "lead": facts.get("name") or cust_id,
                    "reason": f"unsubstantiated offer language: {_offer_viol[0]}",
                }
            )
            continue

        # ANTI-FAKE-PERSONALIZATION (ju1.3, the anti-theater core): every second-person
        # claim about the customer (interest / objection / tattoo-history / social /
        # artist-preference) must be grounded in a fact actually on file for this lead.
        # Catches BOTH paths at one chokepoint — the deterministic path is already
        # field-gated (a no-op here), so this net exists to stop the LLM path from
        # hallucinating a claim the prompt forbade. Skip with a concrete reason; never
        # stage a draft that fakes knowledge.
        _pers_facts = personalization_facts_view(facts, objection=objection_val, profile=profile)
        _pers_viol = personalization_violations(
            f"{draft.get('subject') or ''}\n{draft.get('draft') or ''}", _pers_facts
        )
        if _pers_viol:
            skipped.append(
                {
                    "row": _row,
                    "lead": facts.get("name") or cust_id,
                    "reason": f"fake personalization: {_pers_viol[0]}",
                }
            )
            continue

        # First-class per-lead DOSSIER (P2-C, 65w.7): assemble the evidence-linked record
        # from the REAL facts already gathered (identity/contact, persona, the grounded
        # objection, the chosen angle, the resolved CTA). Pure — no fabrication.
        _cta_kind = (
            "booking-link"
            if any(g == "cta=booking-link" for g in draft.get("grounding", []))
            else "reply-based"
            if draft["channel"] in ("gmail", "email")
            else None
        )
        dossier = build_dossier(
            facts,
            profile=profile,
            angle={
                "label": draft.get("angle"),
                "key": draft.get("angle_key"),
                "generic": draft.get("generic"),
                "inferred": draft.get("inferred"),
            },
            offer=chosen_offer,
            research=research,
            channel=draft["channel"],
            cta_kind=_cta_kind,
            evidence_used=draft.get("grounding", []),
            run_id=run_id,
        )
        # SKILL SELECTION per lead (P2-B, 65w.6): route the dossier to the right first-party
        # marketing play (objection-recovery / re-engagement / loyalty / warm-intro ...).
        # Deterministic. The aligned skillpack IS registered but its eval-gate is PENDING and
        # its loader is DORMANT, so NO pack is loaded/executed and NO pack prose is injected —
        # only the first-party play is used. Recorded as ``skill_used`` evidence.
        selection = select_skill(dossier)

        # TRUTHFUL model provenance: the real model the copy was written with, read from
        # the cell that ran (build_outreach_draft returns it) — never a hardcoded literal
        # that could drift from the copywriter cell's actual pin.
        copy_model = draft.get("copy_model") or "grounded_template"
        _rec(
            "draft",
            copy_model,
            {"customer_id": cust_id, "channel": draft["channel"]},
            {
                "hook": draft.get("subject") or "",
                "headline": draft.get("subject") or "",
                "caption": draft.get("draft") or "",
                "channel": draft["channel"],
                "grounding": draft.get("grounding", []),
                # Per-lead personalization proof (the distinct angle + honest rationale),
                # so the evidence panel can show WHY this draft differs from the others.
                "angle": draft.get("angle"),
                "angle_key": draft.get("angle_key"),
                "why_different": draft.get("why_different"),
                "generic": draft.get("generic"),
                "inferred": draft.get("inferred"),
                # P2-B: the selected marketing skill/play + why (evidence, not a pack load).
                "skill_used": selection.skill_id,
                "skill_why": selection.why,
                "skill_tone": selection.tone,
                "skill_aligned_pack": selection.aligned_pack,
                "skill_pack_status": selection.pack_status,
                # P2-C: the full evidence-linked dossier this draft was written from.
                "dossier": dossier.model_dump(),
                "limited_personalization": dossier.limited_personalization,
            },
        )

        # CRITIC runs PER draft — one REAL independent pass that judges the actual copy
        # that was produced (not the author's reasoning). Recorded as a real `critic`
        # agent_run so the Critic lane reads `done` with real lineage. A cell hiccup
        # records an honest `failed` verdict and continues — never a crash, and never a
        # fabricated approval for a draft the critic could not actually judge.
        caption = draft.get("draft") or draft.get("subject") or ""
        crit_prompt = "\n".join(
            p
            for p in [
                f"Campaign objective: {campaign_angle or goal}",
                f"Channel: {draft['channel']}",
                f"ASSET TO JUDGE (the outreach copy):\n{caption}",
                f"Subject/headline: {draft.get('subject') or ''}",
                "Judge whether this is ship-quality outreach for this lead; flag any "
                "off-voice phrasing, unsupported claim, or weak/absent call to action as "
                "a concrete issue. Do not invent praise.",
            ]
            if p
        )
        # Capture the critic's REAL verdict + confidence so it can land on the draft's
        # conf field (the operator saw conf=None on every draft). A failed critic leaves
        # both None -> honest unknown conf, never a fabricated score.
        crit_verdict: str | None = None
        crit_confidence: float | None = None
        try:
            crit = critic_cell.run_sync(crit_prompt)
            crit_verdict, crit_confidence = crit.verdict.value, float(crit.confidence)
            # A REAL model call succeeded for this lead — the failure streak breaks.
            _model_fail_streak = 0
            _rec(
                "critic",
                _cell_model(critic_cell),
                {"customer_id": cust_id, "channel": draft["channel"]},
                {
                    "verdict": crit_verdict,
                    "confidence": crit_confidence,
                    "rationale": crit.rationale,
                },
            )
        except Exception as exc:  # honest failed verdict, never fabricated praise
            # Circuit-breaker signal: this lead hit a REAL model/HTTP error (in the
            # critic, or already in the draft cell whose copy fell back to template).
            if _is_model_error(exc) or _draft_model_fallback(draft):
                _model_fail_streak += 1
                _last_model_error = f"{type(exc).__name__}: {exc}"
            else:
                _model_fail_streak = 0
            _rec(
                "critic",
                _cell_model(critic_cell),
                {"customer_id": cust_id, "channel": draft["channel"]},
                {
                    "verdict": "error",
                    "confidence": 0.0,
                    "rationale": f"critic cell failed: {type(exc).__name__}: {exc}",
                },
            )

        # MODEL-FAILURE CIRCUIT BREAKER trip check: strategist failed AND the last
        # MODEL_FAILURE_BREAKER_THRESHOLD leads ALL hit real model errors. This lead's
        # draft still stages below (per-draft isolation kept; staged drafts are kept)
        # — the loop then stops at the end of this iteration.
        if (
            not _breaker_tripped
            and _strategist_failed
            and _model_fail_streak >= MODEL_FAILURE_BREAKER_THRESHOLD
        ):
            _breaker_tripped = True

        # Land the critic's quality score on the draft so the Review Queue shows REAL,
        # varying confidence (a generic draft the critic flags scores lower than a
        # well-grounded, approved one); None stays honest-unknown.
        draft_conf = _draft_quality_conf(crit_verdict, crit_confidence)
        # LINK the staged draft to its dossier + selected skill (P2-B/-C): the Review-Queue
        # row carries the evidence-linked dossier in ``context`` so the UI can deep-link from
        # the draft to exactly what we knew about this lead and which play was chosen.
        _context_obj: dict[str, Any] = {
            "skill_used": selection.skill_id,
            "skill_why": selection.why,
            "aligned_pack": selection.aligned_pack,
            "pack_status": selection.pack_status,
            "limited_personalization": dossier.limited_personalization,
            "personalization_note": dossier.personalization_note,
            "dossier": dossier.model_dump(),
        }
        # The operator-SELECTED artwork rides on every staged action (item 3): the
        # UI/evidence shows it, and the gmail delivery layer reads
        # ``attachment_artifact_id`` to attach the real file (wired separately).
        if selected_artwork is not None:
            _context_obj["artwork"] = {
                "assetId": selected_artwork.get("assetId"),
                "artifactId": selected_artwork.get("artifactId"),
                "vlmSummary": selected_artwork.get("vlmSummary"),
            }
            if draft["channel"] in ("gmail", "email") and selected_artwork.get("artifactId"):
                _context_obj["attachment_artifact_id"] = selected_artwork["artifactId"]
        _context = _json.dumps(_context_obj)
        _staged = record_pending_action(
            tenant_id=tenant_id,
            decision_id=None,
            type="outreach",
            channel=draft["channel"],
            worker="studio_provided_leads",
            target=draft["target"],
            draft=draft["draft"],
            subject=draft.get("subject"),
            context=_context,
            conf=draft_conf,
            threshold=None,
            esc_kind="approval_required",
            esc_label="Provided-lead outreach — operator approval required",
            idempotency_key=f"{run_id}:{cust_id}",
            run_id=run_id,
            dsn=dsn,
            with_created=True,
        )
        # Tolerate seam fakes that predate with_created (a bare id == created).
        action_id, _created = (
            _staged if isinstance(_staged, tuple) else (_staged, True)
        )
        if not _created:
            # The one-pending-draft-per-recipient guard absorbed this insert: the
            # recipient already has a HELD draft (usually staged by an earlier run
            # in the same session). Count it in the ledger — a silent reuse read as
            # "N unaccounted" on the reconciliation panel (a real operator hit 24).
            skipped.append({
                "row": None,
                "lead": facts.get("name") or cust_id,
                "reason": (
                    f"already has a pending draft in the Review Queue "
                    f"(action {action_id}, staged by an earlier run) — not duplicated"
                ),
            })
            if _durable is not None:
                try:
                    _durable.step(
                        _step_key,
                        lambda conn, _aid=action_id, _cid=cust_id: {
                            "action_id": _aid,
                            "customer_id": _cid,
                            "staged": False,
                            "reused_existing": True,
                        },
                    )
                except Exception:
                    pass
            if _breaker_tripped:
                break
            continue
        pending.append(action_id)
        try:
            store.write(
                tenant_id=tenant_id,
                subject_type="customer",
                subject_id=cust_id,
                text=(
                    f"Staged {draft['channel']} outreach to {facts.get('name')} for goal "
                    f"'{goal}'. Grounded on: {', '.join(draft.get('grounding', []))}."
                ),
                metadata={
                    "kind": "outreach",
                    "session_id": session_id,
                    "action_id": action_id,
                    "run_id": run_id,
                },
            )
        except Exception:
            pass
        # Mark this lead durably processed (fr1.2). The action row above already
        # committed under its idempotency key; this ledger record is what a restart
        # reads to SKIP the (expensive) re-draft. A crash between the two leaves the
        # action row without a ledger row -> restart re-drafts but the idempotency
        # key blocks a second row (wasted work, never a double side-effect). The
        # fn writes nothing on ``conn`` (record_pending_action used its own), so this
        # step is a pure replay-skip marker per docs/design/p3-durable-hitl.md §5.1.
        if _durable is not None:
            try:
                _durable.step(
                    _step_key,
                    lambda conn, _aid=action_id, _cid=cust_id: {
                        "action_id": _aid,
                        "customer_id": _cid,
                        "staged": True,
                    },
                )
                _durable.checkpoint(cursor=len(pending))
            except Exception:
                pass  # ledger is best-effort; the idempotency key is the real guard

        if _breaker_tripped:
            break  # model calls are failing consistently — stop the fan-out honestly

    # MODEL-FAILURE CIRCUIT BREAKER (post-loop record): ONE honest supervisor step
    # naming what happened + a counted skip row so the output ledger still reconciles.
    # The run is marked FAILED with this reason below (summary + materialized runs row).
    _breaker_note: str | None = None
    if _breaker_tripped:
        _n_processed = len(_sup_processed_ids)
        _breaker_note = (
            f"stopped after {_n_processed} lead(s): model calls are failing "
            f"consistently (last error: {_last_model_error or 'model/HTTP error'}) — "
            "check the ANTHROPIC key/credits and relaunch; drafts already staged "
            "are kept"
        )
        _rec(
            "supervisor",
            "circuit_breaker:deterministic",
            {
                "rule": "model_failure_circuit_breaker",
                "consecutive_model_failures": _model_fail_streak,
                "threshold": MODEL_FAILURE_BREAKER_THRESHOLD,
            },
            {
                "stopped": True,
                "finding": _breaker_note,
                "last_error": _last_model_error,
                "leads_processed": _n_processed,
                "drafts_staged": len(pending),
            },
        )
        _already_accounted = sum(int(s.get("count", 1) or 1) for s in skipped)
        try:
            _left = max(int(expected) - len(pending) - _already_accounted, 0)
        except (TypeError, ValueError):
            _left = 0
        if _left:
            skipped.append(
                {
                    "row": None,
                    "lead": "run",
                    "count": _left,
                    "reason": (
                        "stopped by the model-failure circuit breaker before drafting "
                        f"({_left} lead(s) not attempted)"
                    ),
                }
            )

    # 2a-bis) COHORT-CLAIM CONFORMANCE (truth-gap fix): the cohort is finalized —
    # compare what the plan CLAIMS about it (requested artist / assumed objection)
    # against the selected leads' REAL attributes (customers.artist; the analysts'
    # classified objections where available). A divergence lands as a supervisor-
    # visible run step AND a plan-summary note ('0 of 3 selected leads have a Keebs
    # history; …'). It surfaces the truth; it NEVER blocks the run.
    cohort_note: str | None = None
    try:
        from studio.supervisor_control import check_cohort_claim

        try:
            from studio.artists_directory import list_artists

            _roster = [
                str(a.get("name")) for a in list_artists(tenant_id, dsn=dsn) if a.get("name")
            ]
        except Exception:
            _roster = []
        _objs_by_lead = {
            (ar.get("input") or {}).get("customer_id"):
                (ar.get("output") or {}).get("primary_objection")
            for ar in agent_runs
            if ar.get("role") == "analyst"
        }
        _claim = check_cohort_claim(plan, _cohort_seen, _objs_by_lead, roster=_roster)
        if _claim is not None:
            cohort_note = f"note: {_claim['detail']} — {_claim['question']}"
            _rec(
                "supervisor",
                "conformance:cohort",
                {"rule": _claim["rule"]},
                {
                    "finding": _claim["detail"],
                    "question": _claim["question"],
                    "blocking": False,
                },
            )
    except Exception:
        cohort_note = None  # the note is additive evidence; never break a real run

    # 2b) PROGRESS-AWARE REPLAN (P1.5 blueprint #3, limited-commitment — NOT reflect-every-
    # step). After the loop has accumulated real evidence, ``maybe_replan`` compares the
    # analyst's MEASURED dominant objection against the blueprint's ASSUMPTION under HARD
    # gates (sample ≥ MIN_SAMPLE, margin ≥ MIN_MARGIN, measured ≠ assumed, replans <
    # REPLAN_CAP). It returns a CONCRETE PlanDelta (from ≠ to) or None — a decorative
    # no-diff replan is impossible. The replan is recorded ONCE as a planner agent_run with
    # a DETERMINISTIC id (exactly-once via ON CONFLICT DO NOTHING); already-staged HELD
    # drafts are NOT re-drafted (exactly-once). The delta flips the blueprint's assumption/
    # angle for a NEXT batch only.
    contradictions: list[str] = []
    delta = maybe_replan(blueprint, agent_runs, replans_so_far=0)
    if delta is not None:
        contradictions.append(delta.reason)
        sample_n = sum(
            1
            for ar in agent_runs
            if ar["role"] == "analyst"
            and (ar["output"].get("objection_signal") in ("stated", "inferred"))
        )
        replan_out = {
            "phase": "replan",
            "replan": {
                "contradiction": delta.reason,
                "from_objection": delta.from_objection,
                "to_objection": delta.to_objection,
                "new_offer_code": delta.new_offer_code,
                "new_angle": delta.new_angle,
            },
        }
        # Deterministic id → exactly-once (a re-run with the same measurement won't double-
        # record). record_agent_run is ON CONFLICT DO NOTHING; mirror into agent_runs so the
        # summary/board see the replan too.
        rid = replan_event_id(run_id, delta.from_objection, delta.to_objection, sample_n)
        ts.record_agent_run(
            id=rid,
            campaign_id=campaign_id,
            run_id=run_id,
            role="planner",
            model=blueprint.planner_model,
            input={"phase": "replan", "assumed_dominant_objection": delta.from_objection},
            output=replan_out,
        )
        agent_runs.append(
            {
                "role": "planner",
                "model": blueprint.planner_model,
                "input": {"phase": "replan"},
                "output": replan_out,
                "output_summary": _summarize_output("planner", replan_out),
            }
        )
        # Apply the delta to the in-memory blueprint (the plan the summary returns) and
        # re-persist the authored plan row; the replan EVENT itself is the deterministic
        # agent_run above.
        blueprint.assumed_dominant_objection = delta.to_objection
        if delta.new_angle:
            blueprint.angle = delta.new_angle
        _persist_blueprint()

    # 2c) Compute the durable PROGRESS BOARD ON DEMAND from the SAME real rows (no board
    # table — the board is derived, never a second source of truth).
    try:
        from actions.store import list_actions_for_run

        run_actions_rows = list_actions_for_run(run_id, dsn=dsn)
    except Exception:
        run_actions_rows = []
    # OUTPUT-COUNT LEDGER (P2-D, 65w.8): reconcile drafted vs expected with a per-row skip
    # ledger — "8 of 10 — rows 3,7 skipped: no email address". Reconciled = every expected
    # row is either drafted or has a concrete skip reason (no silent undercount).
    try:
        expected_n = int(expected)
    except (NameError, TypeError, ValueError):
        expected_n = len(pending) + len(skipped)
    # A skip row normally accounts for ONE requested row; a cohort-shortfall row carries
    # an explicit ``count`` accounting for EVERY missing slot (nmh.1) so the ledger
    # balances (created + accounted-skips == requested), never a silent undercount.
    accounted_skips = sum(int(s.get("count", 1) or 1) for s in skipped)
    output_ledger = {
        "expected": expected_n,
        "drafted": len(pending),
        "skipped": skipped,
        "skipped_count": accounted_skips,
        "reconciled": (len(pending) + accounted_skips) >= expected_n,
    }

    board = board_for_run(run_id, None, agent_runs, run_actions_rows, plan, ledger=output_ledger)

    # 3) A final jury summary over the per-lead drafts (offline aggregate, HELD). Carries
    # the output-count ledger so the on-demand board can derive it from this real row too.
    _skip_phrase = ""
    if skipped:
        _rows = ", ".join(str(s["row"]) for s in skipped if s.get("row"))
        _reasons = "; ".join(sorted({str(s["reason"]) for s in skipped}))
        _skip_phrase = (
            f"; {len(skipped)} skipped" + (f" (rows {_rows})" if _rows else "") + f": {_reasons}"
        )
    # FAIL-CLOSED (0dy/37y): the jury cannot certify drafts on top of a required gate that
    # FAILED. If the strategist or the critic could not run (credit-out), the jury records
    # an HONEST ``blocked`` verdict (aggregate 0.0) — drafts stay pending_review, NOT
    # approved — instead of the old fake ``aggregate=1.0`` whenever drafts merely existed.
    _upstream_failures = required_step_failures(agent_runs, ("strategist", "critic"))
    if _upstream_failures:
        _blocked_gates = ", ".join(sorted({f["agent"] for f in _upstream_failures}))
        _jury_output = {
            "aggregate": 0.0,
            "decision": "blocked",
            "status": "failed",
            "error": (
                f"cannot certify drafts: required step(s) failed ({_blocked_gates}); "
                f"drafts held pending_review, not approved"
            ),
            "output_ledger": output_ledger,
            "note": (
                f"{len(pending)} draft(s) staged but NOT approved — {_blocked_gates} "
                f"failed; run held for retry (nothing sent)"
            ),
        }
    else:
        _jury_output = {
            "aggregate": 1.0 if pending else 0.0,
            "decision": "review",
            "output_ledger": output_ledger,
            "note": (
                f"{len(pending)} of {expected_n} per-lead draft(s) staged HELD from "
                f"{source_note}{_skip_phrase}; approve-first — nothing sent"
            ),
        }
    # HONEST TRACE (65w.15): this verdict was computed by pure code above — record the
    # deterministic label, never a model id claiming a jury that never ran.
    from autonomy.jury import DETERMINISTIC_JURY_MODEL

    _rec(
        "jury",
        DETERMINISTIC_JURY_MODEL,
        {"n_leads": len(leads), "lead_source": "provided"},
        _jury_output,
    )

    # The FAIL-CLOSED terminal status decided from the REAL agent_runs (incl. the honest
    # jury above): 'failed' when any required gate failed, else 'completed'. The runs row,
    # the in-memory registry (_bg), and the summary all read from THIS — never a hardcoded
    # 'completed'. The failure_summary surfaces agent/step/error/retryable/can_continue/impact.
    run_status = campaign_run_status(agent_runs, FAILCLOSED_REQUIRED_ROLES)
    failure_summary = required_step_failures(agent_runs, FAILCLOSED_REQUIRED_ROLES)
    # The circuit breaker is a hard failure with an explicit reason: the run is FAILED
    # (never 'completed') and the honest stop note rides the failure_summary so both
    # the summary and the materialized runs row carry WHY the loop stopped.
    if _breaker_note:
        run_status = "failed"
        failure_summary = list(failure_summary) + [
            {
                "agent": "supervisor",
                "step_id": "model_failure_circuit_breaker",
                "error": _breaker_note,
                "retryable": True,
                "can_continue": False,
                "impact": (
                    f"{len(pending)} staged draft(s) kept HELD; remaining leads "
                    "not attempted"
                ),
            }
        ]

    runs_row = _materialize_runs_row(
        dsn=dsn,
        run_id=run_id,
        tenant_id=tenant_id,
        agent_runs=agent_runs,
        terminal_status=run_status,
    )

    # Mirror the run into the chat thread as ONE collapsed, client-readable turn per
    # role (tlv.3; same as the spine path) — the per-lead detail stays on agent_runs.
    for role, text, model in chat_mirror_turns(agent_runs):
        role = role if role in VALID_ROLES else "host"
        _log_turn(dsn, session_id, role, text, model)

    channels = sorted(
        {
            str(ar["input"].get("channel"))
            for ar in agent_runs
            if ar["role"] == "draft" and ar["input"].get("channel")
        }
    )
    n_critics = sum(1 for ar in agent_runs if ar["role"] == "critic")
    n_analysts = sum(1 for ar in agent_runs if ar["role"] == "analyst")
    n_offers = sum(
        1
        for ar in agent_runs
        if ar["role"] == "draft"
        and any(str(g).startswith("offer=") for g in (ar["output"].get("grounding") or []))
    )
    plan.tasks_per_role = {
        "analyst": [f"psych-analyzed {n_analysts} lead(s): category + grounded objection"],
        "strategist": [
            f"angle: {campaign_angle}" if campaign_angle else "campaign strategy step recorded"
        ],
        "researcher": [f"researched {len(leads)} provided lead(s) from {source_note}"],
        "draft": [f"{len(pending)} per-lead brand-voiced draft(s) staged HELD"],
        "critic": [f"{n_critics} independent critic pass(es) over the staged draft(s)"],
    }
    try:
        _persist_plan(dsn, session_id, plan)
    except Exception:
        pass

    # CAMPAIGN MEMORY (nmh.6, spec §18): record THIS run as durable campaign memory so
    # the next campaign for the same artist can reuse it ("last time we ran X"). A HELD
    # run has staged (not sent) drafts, so recipient_count = drafts staged and delivered
    # stays 0 — an honest record of what the run PRODUCED. Best-effort: a memory-write
    # hiccup must never break the real run.
    try:
        from studio.campaign_memory import record_run_campaign

        _artists = [f.get("artist") for f in leads if f.get("artist")]
        _dominant_artist = max(set(_artists), key=_artists.count) if _artists else None
        record_run_campaign(
            tenant_id,
            campaign_name=f"{goal} ({campaign_id})",
            artist=_dominant_artist,
            recipient_count=len(pending),
            delivered_count=0,
            failed_count=len(skipped),
            categories=list(channels) or None,
            status=run_status,
            run_id=run_id,
            dsn=dsn,
        )
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
        # P2-D: the output-count reconciliation ledger (expected vs drafted + row-level
        # skip reasons) so the UI can say "8 of 10 — rows 3,7 skipped: no email address".
        "output_ledger": output_ledger,
        "channels": channels,
        "blueprint": blueprint.model_dump(),
        "board": board.model_dump(),
        "artwork": selected_artwork,
        "artwork_note": artwork_note,
        # Cohort-claim conformance (truth-gap fix): the supervisor-visible divergence
        # note when the selected leads don't match the plan's artist/objection claim
        # (None = the cohort matches the claim). Also emitted below in step_notes.
        "cohort_note": cohort_note,
        "step_notes": ([cohort_note] if cohort_note else [])
        + ([f"circuit breaker: {_breaker_note}"] if _breaker_note else [])
        + ([f"artwork: {artwork_note}"] if artwork_note else [])
        + ([
            f"artwork attached to every staged draft: asset {selected_artwork.get('assetId')}"
            + (f" ({selected_artwork.get('vlmSummary')})" if selected_artwork.get("vlmSummary") else "")
        ] if selected_artwork else [])
        + [
            f"planner built the executable blueprint first (target '{blueprint.targets.category}', "
            f"quota {blueprint.stop_conditions.total_quota or 'uncapped'}, "
            f"{sum(1 for r in blueprint.offer_logic if r.offer_code)} objection(s) with a real offer) "
            f"[{blueprint.planner_model}]",
            f"lead_source=provided: targeting ONLY the operator's leads — {source_note}",
            f"analyst psych-analyzed {n_analysts} lead(s) per-lead (category + grounded objection, no fabrication)",
            f"strategist set the campaign angle once ({campaign_angle or 'recorded'})",
            f"researched {len(leads)} lead(s) per-lead (DB history + cited web research about each)",
            f"staged {len(pending)} brand-voiced draft(s) HELD (approve-first); nothing sent"
            + (f"; {n_offers} referenced a REAL substantiated offer" if n_offers else ""),
            f"critic ran {n_critics} independent pass(es) over the staged draft(s)",
            f"output count: {len(pending)} of {expected_n} drafted"
            + (_skip_phrase.replace("; ", "", 1) if _skip_phrase else " (all rows accounted for)"),
        ],
        "runs_row": runs_row,
        # Fail-closed outcome (0dy/37y): 'completed' only when every required gate passed;
        # 'failed' + surfaced failure_summary when a required step (strategist/critic/jury)
        # errored. _bg marks the run registry from this — never a hardcoded 'completed'.
        "run_status": run_status,
        "failure_summary": failure_summary,
    }


def _persist_campaign_spec(
    plan: CampaignPlan,
    summary: dict[str, Any],
    session_id: str,
    tenant_id: str,
    dsn: str | None,
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
                    "steps_enabled": sorted(getattr(s, "value", s) for s in aspec.steps_enabled),
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


# The FastAPI app the studio is mounted on — stashed by ``mount_studio_agui`` so the
# chat host's ``run_campaign`` tool can launch a background run on the SAME registry
# the ``GET /studio/run/{id}`` poller reads. When no app is mounted (hermetic tests /
# direct agent runs) a module-level stand-in keeps the registry alive in-process.
_MOUNTED_APP: Any | None = None
_FALLBACK_APP: Any | None = None


def _background_app() -> Any:
    """The app object background launches register on: the mounted app when the
    studio is mounted, else a persistent module-level stand-in (same shape: it only
    needs ``.state``), so the launch path is identical either way."""
    global _FALLBACK_APP
    if _MOUNTED_APP is not None:
        return _MOUNTED_APP
    if _FALLBACK_APP is None:
        from types import SimpleNamespace

        _FALLBACK_APP = SimpleNamespace(state=SimpleNamespace())
    return _FALLBACK_APP


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

    campaign_id = f"camp_{uuid.uuid4().hex[:12]}"
    run_id = f"team-{campaign_id}-{uuid.uuid4().hex[:12]}"

    try:
        await asyncio.to_thread(_log_turn, dsn, session_id, "operator", trigger_note, None)
    except Exception:
        pass

    start_registered_run(app, dsn, session_id, tenant_id, plan, run_id)
    return {"runId": run_id, "campaignId": campaign_id, "status": "running"}


def start_registered_run(
    app, dsn: str | None, session_id: str, tenant_id: str, plan: CampaignPlan, run_id: str
) -> None:
    """Register ``run_id`` as running and execute the campaign in the background —
    shared by the fresh launch (:func:`launch_studio_run`) AND the artwork-selection
    RESUME (``POST /studio/campaign/{run_id}/select-artwork``), which re-invokes the
    executor with the SAME run id (the durable replay-skip + deterministic one-shot
    agent-run ids make the resume idempotent). Must be called from an event loop."""
    if not hasattr(app.state, "_studio_runs"):
        app.state._studio_runs = {}
    runs_registry: dict[str, dict] = app.state._studio_runs
    # tenant_id/session_id ride on the registry entry so the supervisor fleet board can
    # attribute an IN-FLIGHT run (agent_runs steps, no runs row yet) to its real tenant.
    runs_registry[run_id] = {
        "status": "running", "summary": None, "error": None,
        "tenant_id": tenant_id, "session_id": session_id,
    }
    try:  # let the live-state tools report this in-flight run's status (item 4)
        from studio.live_state import set_runs_registry

        set_runs_registry(runs_registry)
    except Exception:
        pass

    async def _bg() -> None:
        try:
            summary = await asyncio.to_thread(
                _execute_campaign_sync, plan, session_id, tenant_id, dsn, run_id
            )
            # FAIL-CLOSED (0dy): mark the registry from the summary's real terminal status.
            # A run whose required gate (strategist/critic/jury) failed reads 'failed' with
            # its surfaced failure_summary — NEVER a hardcoded 'completed' over an errored run.
            _run_status = summary.get("run_status") or "completed"
            _failures = summary.get("failure_summary") or []
            # 'not_built' (nmh.9) is an HONEST terminal, not a failure — a routed
            # channel with no pipeline yet. 'awaiting_selection' (item 3) is a PAUSE
            # (the operator's artwork pick resumes it). Neither carries an error.
            runs_registry[run_id] = {
                "status": _run_status,
                "summary": summary,
                "error": (
                    None
                    if _run_status in ("completed", "not_built", "awaiting_selection")
                    else "; ".join(f"{f['agent']}: {f['error']}" for f in _failures)
                    or "required step failed"
                ),
                "tenant_id": tenant_id,
                "session_id": session_id,
            }
            try:
                await asyncio.to_thread(
                    _log_turn, dsn, session_id, "host", _summary_text(summary), HOST_AGUI_MODEL
                )
            except Exception:
                pass
        except Exception as exc:  # honest failure, never a fake success
            runs_registry[run_id] = {
                "status": "error",
                "summary": None,
                "error": f"{type(exc).__name__}: {exc}",
                "tenant_id": tenant_id,
                "session_id": session_id,
            }
            # FAIL LOUD IN THE THREAD: the operator was told "launched — I'll post
            # the summary here". A crash that only lands in the in-memory registry
            # is dead air (the host said LIVE, then nothing ever arrived) — post the
            # honest failure as a host turn so the conversation itself says so.
            try:
                await asyncio.to_thread(
                    _log_turn, dsn, session_id, "host",
                    f"Run {run_id} FAILED before completing: "
                    f"{type(exc).__name__}: {exc}. No drafts were staged by this "
                    "run. Fix the cause and launch again — nothing was sent.",
                    HOST_AGUI_MODEL,
                )
            except Exception:
                pass

    asyncio.create_task(_bg())


@studio_agent.tool
async def run_campaign(ctx: RunContext[StudioDeps]) -> str:
    """LAUNCH the REAL, traced campaign for the CURRENT plan in the BACKGROUND and
    return IMMEDIATELY with the run id. Call when the operator asks to RUN / launch /
    execute / kick off the campaign (or approves the plan to run).

    The launch reuses the SAME background path the ``POST /studio/run`` button uses
    (:func:`start_registered_run`): the run registers on the shared registry, executes
    the WIRED Phase-A spine off-thread (research -> strategy -> draft x N -> independent
    critique -> route pinned to HOLD -> queue), and its per-agent steps land in
    ``agent_runs`` incrementally — watchable live via the existing
    ``GET /studio/run/{run_id}`` polling (the Agency tab). Drafts still land HELD /
    PENDING behind approve-first; NOTHING IS SENT. When the run finishes, its honest
    summary is persisted as a host turn in this thread.

    This tool no longer blocks the chat stream for the whole multi-agent run (the old
    behavior left the operator staring at dead air for minutes). Reply to the operator
    NOW with the run id and where to watch; do NOT claim any drafts exist yet."""
    campaign_id = f"camp_{uuid.uuid4().hex[:12]}"
    run_id = f"team-{campaign_id}-{uuid.uuid4().hex[:12]}"
    start_registered_run(
        _background_app(),
        ctx.deps.dsn,
        ctx.deps.session_id,
        ctx.deps.tenant_id,
        ctx.deps.state,
        run_id,
    )
    launch = {"run_id": run_id, "status": "launched", "watch": "Agency tab"}
    return (
        f"{json.dumps(launch)}\n"
        f"Campaign run {run_id} is LAUNCHED and executing in the background — nothing "
        "has been drafted or sent yet. The operator can watch each agent land live in "
        "the Agency tab (the run view already polls this run id). Drafts will stage "
        "HELD in the Review Queue behind approve-first, and I will post the run's "
        "honest summary into this thread when it finishes. Tell the operator the run "
        "id and where to watch; do not invent results."
    )


@studio_agent.tool
async def generate_example_campaign(
    ctx: RunContext[StudioDeps],
    artist: str,
    offer_price_usd: int | None = None,
    payment_plan: str | None = None,
    spots: int | None = None,
    follow_up: bool = True,
) -> str:
    """Generate an EXAMPLE-GROUNDED campaign for one ARTIST, built on that artist's REAL
    past campaigns (the ju1.4 generator). Call this when the operator asks to generate /
    create / write a campaign FOR a specific artist (e.g. "generate an Angel $1200
    full-day-special campaign", "write a campaign for Bella").

    Produces an SMS opener + scarcity follow-up (plus email variants), each mirroring the
    artist's real opener/follow-up examples and CITING the real campaign-example ids,
    staged HELD in the Review Queue — nothing is sent and the test-mode gate still refuses
    every live send. OFFER DISCIPLINE: pass ``offer_price_usd`` / ``payment_plan`` /
    ``spots`` ONLY when the operator actually stated them — NEVER invent a price or a spot
    count; with no offer the copy carries no price. An artist with no examples is generated
    from the studio's overall patterns, stated honestly. Returns a short honest summary."""
    from studio.campaign_generator import generate_campaign, stage_campaign

    def _run() -> tuple[Any, str, list[str]]:
        campaign = generate_campaign(
            ctx.deps.tenant_id,
            artist=artist,
            offer_price_usd=offer_price_usd,
            payment_plan=payment_plan,
            spots=spots,
            follow_up=follow_up,
            dsn=ctx.deps.dsn,
        )
        run_id = f"studio-gen-{ctx.deps.session_id}-{uuid.uuid4().hex[:8]}"
        staged = stage_campaign(campaign, run_id=run_id, dsn=ctx.deps.dsn)
        return campaign, run_id, staged

    campaign, run_id, staged = await asyncio.to_thread(_run)
    await asyncio.to_thread(
        _log_turn, ctx.deps.dsn, ctx.deps.session_id, "host",
        f"Generated {artist}'s example-grounded campaign — {len(staged)} draft(s) staged "
        f"HELD for your review, grounded on {len(campaign.grounded_example_ids)} real past "
        f"campaign example(s). Details in the Runs tab.",
        HOST_AGUI_MODEL,
    )
    ex = ", ".join(campaign.grounded_example_ids) or "none on file for this artist"
    lines = [
        campaign.pattern_summary,
        "",
        f"I staged {len(staged)} draft(s) for {artist} into your Review Queue, HELD for "
        "your approval — nothing is sent (test mode refuses every live send). Each draft is "
        f"grounded in {artist}'s real past examples: {ex}.",
    ]
    if campaign.notes:
        lines.append("Notes: " + " ".join(campaign.notes))
    return "\n".join(lines)


@studio_agent.tool(requires_approval=True)
async def stage_publish(
    ctx: RunContext[StudioDeps], channel: str, draft: str, target: str | None = None
) -> str:
    """Stage a would-SEND action behind the HOLD gate. ``requires_approval=True`` so
    pydantic-ai surfaces this as an UNAPPROVED deferred request — it never
    auto-fires. Even after approval this only writes a PENDING ``actions`` row; the
    real send stays held on the existing approve-first path. NOTHING is sent here."""
    from actions.store import ensure_schema, record_pending_action
    from cells.identity_guard import foreign_identity_violations

    # FOREIGN-IDENTITY GATE (wwy.7 r8): this stages SUPERVISOR-authored free text. Refuse
    # to stage a draft that names ANOTHER tenant's studio/handle for this tenant — the
    # exact "it's Rae from Ladies First" bleed, one composition path removed from the
    # per-lead loops. Refuse honestly rather than silently staging a fabricated identity.
    _id_viol = foreign_identity_violations(draft, ctx.deps.tenant_id)
    if _id_viol:
        return (
            "REFUSED (not staged): the draft names another studio's identity — "
            f"{_id_viol[0]}. Rewrite it in THIS tenant's own voice (no other studio's "
            "name or handle) and stage again. Nothing was written."
        )

    dsn = ctx.deps.dsn or os.environ.get("ENGINE_DATABASE_URL")
    await asyncio.to_thread(ensure_schema, dsn)

    # LINEAGE FIX (engine-core item 5): the old path staged with NO run_id and a
    # random-uuid idempotency key, so these drafts sorted LAST in the review queue
    # ("Unassigned", no lineage). Derive a REAL campaign/run id (same camp_/team-
    # format launch_studio_run mints), record a minimal agent_runs + runs trail so
    # the draft deep-links to a run like every other staged action, and key the row
    # deterministically (run_id + target/draft hash) — exactly-once inside the run.
    campaign_id = f"camp_{uuid.uuid4().hex[:12]}"
    run_id = f"team-{campaign_id}-{uuid.uuid4().hex[:12]}"
    target_hash = hashlib.sha1(f"{target or ''}|{draft}".encode("utf-8")).hexdigest()[:12]
    idem_key = f"{run_id}:{target_hash}"

    def _record_lineage() -> None:
        """Best-effort minimal trail: one draft agent_run (the supervisor host is the
        real author/model) + a materialized runs row. Never blocks the staging."""
        try:
            from team.store import TeamStore

            from studio.campaign_runner import _materialize_runs_row

            ts = TeamStore(dsn)
            ts.setup()
            out = {
                "caption": draft,
                "channel": channel,
                "source": "stage_publish",
                "note": "supervisor-authored draft staged HELD via the approval gate",
            }
            ts.record_agent_run(
                id=f"ar_stage_{hashlib.sha1(idem_key.encode()).hexdigest()[:16]}",
                campaign_id=campaign_id,
                run_id=run_id,
                role="draft",
                model=HOST_AGUI_MODEL,
                input={"channel": channel, "target": target, "source": "stage_publish"},
                output=out,
            )
            _materialize_runs_row(
                dsn=dsn,
                run_id=run_id,
                tenant_id=ctx.deps.tenant_id,
                agent_runs=[{"role": "draft", "model": HOST_AGUI_MODEL,
                             "input": {"channel": channel}, "output": out}],
                terminal_status="completed",
            )
        except Exception:
            pass

    await asyncio.to_thread(_record_lineage)
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
            idempotency_key=idem_key,
            run_id=run_id,
            dsn=dsn,
        )
    )
    return (
        f"STAGED (held): action {action_id} on {channel} is PENDING approval "
        f"(run {run_id}). Nothing has been sent."
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

    from cells.identity_guard import foreign_identity_violations
    from cells.personalization_guard import facts_view as personalization_facts_view
    from cells.personalization_guard import personalization_violations
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
        leads = lookup_leads(tenant_id, [{"email": e} for e in emails], dsn=dsn, memory_store=store)
        requested = len(emails)
    else:
        leads = churn_risk_leads(tenant_id, limit=limit, dsn=dsn, memory_store=store)
        requested = len(leads)

    goal = plan.goal or "win back lapsed clients"
    # BATCH-STABLE run id (CustomerAcq-nmh.2): every staged draft is keyed to a run so
    # (a) each DISTINCT campaign intent stages fresh rows — the old
    # ``studio:{session}:{cust}:outreach`` key had no run/goal discriminator, so a
    # second campaign for the same customer collided and was SILENTLY dropped by
    # ``ON CONFLICT DO NOTHING``; and (b) the staged ``actions`` row carries a real
    # ``run_id`` so its campaign/run/evidence deep-links resolve (§15).
    #
    # The id is DERIVED from (session, goal, target-set) — NOT a fresh uuid — so it is
    # STABLE across a crash-then-re-drive of the SAME request: re-staging hits the same
    # ``{run_id}:{cust_id}`` keys and ``ON CONFLICT DO NOTHING`` makes it exactly-once
    # (no double-stage). A genuinely different request (different goal / target set)
    # derives a different id and stages fresh. This re-keys STAGING only; SEND
    # exactly-once is guarded separately by ``claim_for_send`` on the action id.
    _batch_seed = "|".join([
        session_id, goal,
        ",".join(sorted(emails)) if emails else f"cohort:{limit}",
    ])
    run_id = f"studio-stage-{hashlib.sha1(_batch_seed.encode()).hexdigest()[:16]}"
    staged: list[dict[str, Any]] = []
    # Guard the reported count against over-counting (nmh.2 honesty gate): a lead that
    # appears twice in the batch, or one already staged under this run, resolves to the
    # SAME action row via ON CONFLICT — count each distinct landed row ONCE so
    # ``n_drafts`` equals the number of rows that actually appear in the Review Queue.
    seen_action_ids: set[str] = set()
    skipped: list[dict[str, Any]] = []
    for facts in leads:
        draft = build_outreach_draft(
            facts, goal=goal, tenant_id=tenant_id, plan_channels=plan.channels or None
        )
        cust_id = facts["customer_id"]

        # ANTI-FABRICATION GATE (wwy.7 r8, the smoking gun): this path staged three
        # skindesign drafts signed "it's Rae from Ladies First" that implied a
        # relationship the DB cannot back — it was the ONE staging loop with no
        # guards. Same net as the provided-leads loop: a draft that asserts another
        # tenant's identity or an ungrounded personalization/relationship claim is
        # SKIPPED with a concrete reason; it never reaches the pending queue.
        _copy_text = f"{draft.get('subject') or ''}\n{draft.get('draft') or ''}"
        _id_viol = foreign_identity_violations(_copy_text, tenant_id)
        if _id_viol:
            skipped.append({"lead": facts.get("name") or cust_id,
                            "reason": _id_viol[0]})
            continue
        _pers_viol = personalization_violations(
            _copy_text, personalization_facts_view(facts)
        )
        if _pers_viol:
            skipped.append({"lead": facts.get("name") or cust_id,
                            "reason": f"fake personalization: {_pers_viol[0]}"})
            continue

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
            idempotency_key=f"{run_id}:{cust_id}",
            run_id=run_id,
            dsn=dsn,
        )
        # A repeated lead (dup in the batch, or already staged under this run) resolves
        # to the SAME action row — count it once so n_drafts == rows in the queue.
        if action_id in seen_action_ids:
            continue
        seen_action_ids.add(action_id)
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
                "angle": draft.get("angle"),
                "why_different": draft.get("why_different"),
                "generic": draft.get("generic"),
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
        "skipped": skipped,
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
    skips = summary.get("skipped") or []
    skip_note = ""
    if skips:
        skip_lines = "; ".join(f"{s['lead']}: {s['reason']}" for s in skips[:5])
        skip_note = (
            f" {len(skips)} draft(s) were REFUSED by the anti-fabrication gate and "
            f"NOT staged — {skip_lines}."
        )
    return (
        f"Researched {summary['n_leads']} lead(s) and staged {summary['n_drafts']} "
        f"personalized PENDING outreach draft(s) across {', '.join(summary['channels']) or 'n/a'} "
        f"— all HELD in the Review Queue, nothing sent.{nf_note}{skip_note} {lead_lines}"
    )


@studio_agent.tool
async def list_conversation_leads(
    ctx: RunContext[StudioDeps], topic: str = "", limit: int = 12
) -> str:
    """The customers whose REAL imported conversation threads are on file — name,
    email, thread length — read fresh from the database. Pass ``topic`` ('price',
    'timing', 'trust', or any keyword) to keep only leads whose OWN words match,
    each returned with the verbatim quote as the receipt. Use this whenever the
    operator says to pick a cohort from the (imported) conversations — e.g.
    'customers who stepped back over price or timing' — then hand the chosen
    emails to `research_and_stage_leads`, or launch `run_campaign` (the run's
    cohort already prioritizes these warm conversation leads). The threads live
    in the DATABASE, not the uploaded-files list — never reply that there is no
    conversation history without calling this first."""
    from studio.customer_research import conversation_lead_index

    rows = await asyncio.to_thread(
        lambda: conversation_lead_index(
            ctx.deps.tenant_id, topic=(topic or None),
            limit=max(1, min(int(limit or 12), 50)), dsn=ctx.deps.dsn,
        )
    )
    if not rows:
        return (
            f"No imported conversation threads have a customer turn matching {topic!r} "
            "— honest miss, do not force the theme." if topic
            else "No imported conversation threads on file for this studio."
        )
    lines = [
        f"IMPORTED CONVERSATION LEADS ({len(rows)} shown, live from the database"
        + (f", topic {topic!r} — each quote is the customer's verbatim words" if topic else "")
        + "):"
    ]
    for r in rows:
        q = f' — their words: "{(r["quote"] or "")[:180]}"' if r.get("quote") else ""
        email = r.get("email") or "no email"
        lines.append(f"- {r['name']} <{email}> · {r['turns']} messages{q}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# LIVE-STATE tools (engine-core item 4, spec §17): the supervisor answers
# "which leads were finalized / what is each agent doing / what files exist /
# which artworks does X have / what changed for X" from FRESH DB reads on every
# call — never a cached or invented answer. Shared with the voice path via
# studio.live_state (the voice seams embed the same snapshot per request).
# --------------------------------------------------------------------------- #


@studio_agent.tool
async def get_run_leads(ctx: RunContext[StudioDeps], run_id: str | None = None) -> str:
    """Which leads were FINALIZED for a campaign run (default: the most recent run):
    the staged drafts' lead names + emails/targets + statuses, and every skipped row
    with its concrete reason. Reads the DB fresh — call this whenever the operator
    asks who the campaign targets / targeted; never answer from memory."""
    from studio.live_state import finalized_leads

    data = await asyncio.to_thread(
        lambda: finalized_leads(ctx.deps.tenant_id, run_id, dsn=ctx.deps.dsn)
    )
    if not data.get("runId"):
        return "No campaign run exists for this studio yet — no leads were finalized."
    lines = [f"Run {data['runId']}: {len(data['staged'])} staged draft(s)."]
    for s in data["staged"][:25]:
        who = s.get("name") or "(name not on file)"
        lines.append(
            f"- {who} -> {s.get('target') or 'no target'} [{s.get('channel')}] "
            f"status={s.get('status')}"
        )
    if data.get("skipped"):
        lines.append(f"Skipped ({len(data['skipped'])}):")
        for s in data["skipped"][:15]:
            lines.append(f"- {s.get('lead')}: {s.get('reason')}")
    else:
        lines.append("Skipped: none recorded.")
    return "\n".join(lines)


@studio_agent.tool
async def get_agent_activity(ctx: RunContext[StudioDeps]) -> str:
    """What each agent is doing RIGHT NOW on the active run — live status (running /
    completed / failed / awaiting_selection) + the latest recorded step per role with
    its real model. Fresh DB read on every call."""
    from studio.live_state import agent_activity

    data = await asyncio.to_thread(
        lambda: agent_activity(ctx.deps.tenant_id, dsn=ctx.deps.dsn)
    )
    if not data.get("runId"):
        return "No campaign run exists for this studio yet — no agents are working."
    lines = [f"Run {data['runId']} status: {data['status']}."]
    sel = data.get("selectionPending")
    if sel:
        lines.append(
            f"WAITING ON THE OPERATOR: {sel.get('question')} "
            f"({len(sel.get('options') or [])} artwork option(s) surfaced)."
        )
    agents = data.get("agents") or {}
    if not agents:
        lines.append("No agent steps recorded yet.")
    for role, info in agents.items():
        last = (info.get("lastOutput") or "").strip()
        lines.append(
            f"- {role} [{info.get('model')}] last step at {info.get('at')}"
            + (f": {last[:160]}" if last else "")
        )
    return "\n".join(lines)


@studio_agent.tool
async def get_uploaded_files(ctx: RunContext[StudioDeps]) -> str:
    """What files/images exist RIGHT NOW: live counts by type + the newest uploads
    including their real VLM descriptions — so 'I added a new tattoo design, which
    one is it?' answers with the newest upload's actual analysis. Fresh read."""
    from studio.live_state import files_snapshot

    data = await asyncio.to_thread(
        lambda: files_snapshot(ctx.deps.tenant_id, dsn=ctx.deps.dsn)
    )
    if not data.get("readable"):
        return (
            "The file store could not be read this turn — say so honestly rather "
            "than guessing a count."
        )
    if not data.get("total"):
        return "No files are uploaded for this studio yet (0 images)."
    by_type = ", ".join(f"{k}={v}" for k, v in (data.get("byType") or {}).items())
    lines = [f"{data['total']} file(s) on record ({by_type}); images: {data['images']}."]
    if data.get("newest"):
        lines.append("Newest uploads (most recent first):")
        for n in data["newest"]:
            desc = n.get("vlmSummary") or "no visual analysis captured"
            artist = f" artist={n['artist']}" if n.get("artist") else ""
            lines.append(f"- {n['name']} [{n['kind']}]{artist} — {desc}")
    return "\n".join(lines)


@studio_agent.tool
async def get_artist_artworks(ctx: RunContext[StudioDeps], artist: str) -> str:
    """Which artworks one ARTIST has in the library (real pieces + their VLM tags).
    Fresh read; an artist with nothing on file reads honestly empty."""
    from studio.live_state import artist_artworks

    data = await asyncio.to_thread(
        lambda: artist_artworks(ctx.deps.tenant_id, artist, dsn=ctx.deps.dsn)
    )
    if not data.get("resolved"):
        return data.get("note") or f"No artist matching {artist!r} in the roster."
    works = data.get("artworks") or []
    if not works:
        return f"{data['artist']} has NO artwork in the library yet — upload one to attach."
    lines = [f"{data['artist']}: {len(works)} piece(s) in the library."]
    for w in works[:12]:
        tags = ", ".join(w.get("styles") or []) or "untagged"
        desc = w.get("vlmSummary") or "no VLM analysis"
        lines.append(f"- asset {w['assetId']} [{tags}] — {desc}")
    return "\n".join(lines)


@studio_agent.tool
async def get_artist_memory(ctx: RunContext[StudioDeps], artist: str) -> str:
    """The most recent MEMORY updates for one artist (uploads, operator notes,
    campaign events) — newest first, real rows only. Fresh read."""
    from studio.live_state import artist_recent_memories

    data = await asyncio.to_thread(
        lambda: artist_recent_memories(ctx.deps.tenant_id, artist, dsn=ctx.deps.dsn)
    )
    mems = data.get("memories") or []
    if not mems:
        return (
            f"No memories recorded for {data.get('artist') or artist} yet — "
            "nothing invented."
        )
    lines = [f"Recent memory for {data['artist']} (newest first):"]
    for m in mems:
        lines.append(f"- [{m['at']}] {m['text']}")
    return "\n".join(lines)


@studio_agent.tool
async def steer_run(
    ctx: RunContext[StudioDeps],
    kind: str,
    run_id: str = "",
    angle: str = "",
    offer_code: str = "",
    customer_id: str = "",
    guidance: str = "",
) -> str:
    """SUPERVISE a live run FULL-DUPLEX: queue a steering directive the executor
    honors at its next safe boundary (before the next lead). Kinds: 'pause',
    'abort', 'set_angle' (+angle), 'set_offer' (+offer_code — substantiation still
    gates it), 'skip_lead' (+customer_id), 'guide_copy' (+guidance). With no run_id
    the ACTIVE run is steered. Every application lands as a visible supervisor step
    in the live panel. Directives only narrow/redirect — never widen delivery."""
    from studio.live_state import agent_activity
    from studio.supervisor_control import issue_directive

    rid = (run_id or "").strip()
    if not rid:
        act = await asyncio.to_thread(
            lambda: agent_activity(ctx.deps.tenant_id, dsn=ctx.deps.dsn)
        )
        rid = str((act.get("activeRun") or {}).get("runId") or "")
        if not rid:
            return "No active run to steer — start a campaign first, or name a run_id."
    payload: dict[str, Any] = {}
    if angle.strip():
        payload["angle"] = angle.strip()
    if offer_code.strip():
        payload["code"] = offer_code.strip()
    if customer_id.strip():
        payload["customer_id"] = customer_id.strip()
    if guidance.strip():
        payload["text"] = guidance.strip()
    try:
        row = await asyncio.to_thread(
            issue_directive,
            rid,
            ctx.deps.tenant_id,
            kind.strip(),
            payload,
            issued_by="supervisor",
            dsn=ctx.deps.dsn,
        )
    except ValueError as exc:
        return f"Could not steer: {exc}"
    return (
        f"Directive {row['kind']} queued for run {rid} — the team applies it before "
        "the next lead and it will show as a supervisor step in the live panel."
    )


@studio_agent.tool(requires_approval=True)
async def schedule_draft(
    ctx: RunContext[StudioDeps], action_id: str, when: str, live: bool = False
) -> str:
    """SCHEDULE one pending Review-Queue draft for a future publish time (RFC3339,
    e.g. 2026-07-11T09:00:00Z). ``requires_approval=True`` — the operator confirms
    before anything is recorded, because a schedule IS an approval with a
    timestamp. Publishing happens through the same gated approve path (TEST-MODE
    gate + redirect still apply); ``live=True`` only requests a non-redirect send
    at publish time and still cannot pass the tenant gate."""
    from studio.scheduler import schedule_action

    try:
        out = await asyncio.to_thread(
            schedule_action, action_id.strip(), when.strip(),
            live=bool(live), dsn=ctx.deps.dsn,
        )
    except ValueError as exc:
        return f"Could not schedule: {exc}"
    return (
        f"Draft {out['actionId']} ({out.get('channel')}, to {out.get('target')}) is "
        f"scheduled for {out['scheduledFor']} ({'LIVE' if out['live'] else 'safe redirect'}). "
        "The scheduler publishes it through the gated approve path at that time."
    )


@studio_agent.tool
async def campaign_intelligence_brief(ctx: RunContext[StudioDeps]) -> str:
    """EXECUTIVE BRIEF: best past campaigns (real delivery numbers), the objection
    landscape from real analyst reads, artist library depth, competitor leaders,
    and evidence-backed recommendations for what to run next. Use this to answer
    'what should we run next and why' — every line is aggregated from real rows."""
    from studio.intelligence import campaign_intelligence

    out = await asyncio.to_thread(
        campaign_intelligence, ctx.deps.tenant_id, dsn=ctx.deps.dsn
    )
    lines = ["EXECUTIVE BRIEF (all numbers from real rows):"]
    for c in out["bestCampaigns"][:3]:
        lines.append(
            f"- campaign {c['campaign_name']!r} ({c.get('artist_name')}): "
            f"{c.get('delivered_count')}/{c.get('recipient_count')} delivered, "
            f"CTA \"{c.get('cta')}\""
        )
    for o in out["objections"][:4]:
        lines.append(f"- objection {o['objection']!r}: {o['leads']} lead(s)")
    for r in out["recommendations"]:
        lines.append(f"- RECOMMEND: {r['recommend']} — WHY: {r['why']}")
    return "\n".join(lines)


@studio_agent.tool
async def fleet_status(ctx: RunContext[StudioDeps]) -> str:
    """FLEET BOARD (initech `status`): every recent run with its live activity —
    working / stalled / waiting-operator / done / failed — plus the last agent
    role that stepped, staged drafts, and pending directives. Use this to answer
    'what is every agent doing right now?' and to spot a stalled or paused run
    before steering it."""
    from studio.supervisor_fleet import fleet_status as _fleet

    board = await asyncio.to_thread(
        lambda: _fleet(ctx.deps.tenant_id, dsn=ctx.deps.dsn)
    )
    if not board:
        return "Fleet is idle: no runs in the last 24h."
    lines = ["FLEET (newest first):"]
    for r in board[:12]:
        age = f"{int(r['last_step_age_s'])}s ago" if r["last_step_age_s"] is not None else "no steps"
        lines.append(
            f"- {r['run_id']} [{r['activity']}] last={r['last_role'] or '-'} ({age}), "
            f"steps={r['n_steps']}, pending drafts={r['n_pending_drafts']}, "
            f"directives pending={r['n_pending_directives']}/applied={r['n_applied_directives']}"
        )
    if len(board) > 12:
        lines.append(f"... and {len(board) - 12} older runs")
    return "\n".join(lines)


@studio_agent.tool
async def review_run(ctx: RunContext[StudioDeps], run_id: str = "") -> str:
    """AUDIT a run's internal coherence from the agents' REAL recorded outputs
    (researcher vs strategist vs analyst): contradiction findings + suggested
    directives. Honest verdict only — apply a fix with steer_run if warranted."""
    from studio.live_state import agent_activity
    from studio.supervisor_control import review_run_coherence

    rid = (run_id or "").strip()
    if not rid:
        act = await asyncio.to_thread(
            lambda: agent_activity(ctx.deps.tenant_id, dsn=ctx.deps.dsn)
        )
        rid = str((act.get("activeRun") or {}).get("runId") or "")
        if not rid:
            return "No run to review — name a run_id or start a campaign."
    v = await asyncio.to_thread(
        lambda: review_run_coherence(rid, ctx.deps.tenant_id, dsn=ctx.deps.dsn)
    )
    if not v.get("contradiction"):
        return (
            f"Reviewed run {rid}: no contradiction found across "
            f"{', '.join(v.get('checked_roles') or [])} (LLM read: {v.get('llm_read')})."
        )
    lines = [f"Reviewed run {rid}: CONTRADICTION found:"]
    for f in v.get("findings") or []:
        lines.append(f"- [{f['rule']}] {f['detail']} → suggest: {f['suggest']}")
    lines.append("Use steer_run to apply a correction.")
    return "\n".join(lines)


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

    # SEMANTIC PROFILE (P1-A): classify the columns into marketing roles and count the
    # REAL segments / objections / social presence over ALL data rows, then attach an
    # honest natural-language summary. Pure (no I/O) — see studio.csv_profiler. Never
    # fabricates: absent dimensions are reported absent, unknown columns named.
    from studio.csv_profiler import build_profile

    all_objs = [_row_to_obj(r) for r in data]
    profile = build_profile(header, all_objs)

    return {
        "ok": True,
        "filename": filename,
        "rows": len(data),
        "columns": header,
        "sample": all_objs[:5],
        "ingested": False,  # honesty: parsed only, not written to the customers table
        # The honest semantic read of the file (roles, real counts, unknown columns) +
        # the natural summary the supervisor states. Attached to the plan by the upload
        # route and surfaced by `_customers_context`.
        "profile": profile.as_dict(),
        "summary": profile.summary_text,
    }


# --------------------------------------------------------------------------- #
# FastAPI mount
# --------------------------------------------------------------------------- #


def _evidence_links(action_id: str, dsn: str | None) -> dict[str, Any]:
    """ADDITIVE evidence keys for one staged action (engine-core item 7): the
    ARTWORK it carries (with a raw-bytes link), the ARTIST it fronts (with the
    artist-API link), and the CUSTOMER dossier link. Everything is read off the
    action's OWN context / dossier — a key appears ONLY when the underlying fact
    exists (real-only; never a fabricated link). Empty dict on any read failure."""
    from actions.store import get_action

    row = get_action(action_id, dsn=dsn)
    if row is None:
        return {}
    try:
        ctx = json.loads(row.context) if row.context else {}
    except Exception:
        ctx = {}
    if not isinstance(ctx, dict):
        ctx = {}
    out: dict[str, Any] = {}

    artwork = ctx.get("artwork")
    if isinstance(artwork, dict) and artwork.get("assetId"):
        link = dict(artwork)
        if artwork.get("artifactId"):
            link["rawUrl"] = f"/studio/artifacts/{artwork['artifactId']}/raw"
        out["artwork"] = link
    if ctx.get("attachment_artifact_id"):
        out["attachmentArtifactId"] = ctx["attachment_artifact_id"]

    artist_name = ctx.get("artist")
    dossier = ctx.get("dossier") if isinstance(ctx.get("dossier"), dict) else {}
    if artist_name:
        try:
            from studio.artists_directory import resolve_artist

            resolved = resolve_artist(row.tenant_id, str(artist_name), dsn=dsn)
        except Exception:
            resolved = None
        out["artist"] = {
            "name": resolved["name"] if resolved else str(artist_name),
            "slug": resolved["slug"] if resolved else None,
            "url": f"/studio/artists/{resolved['slug']}" if resolved else None,
        }

    customer_id = dossier.get("customer_id")
    if customer_id:
        out["customerDossier"] = {
            "customerId": customer_id,
            "url": f"/studio/customer/{customer_id}/dossier?tenant_id={row.tenant_id}",
        }
    return out


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


def _ensure_docs_seeded(app, dsn: str | None, tenant_id: str) -> None:
    """Best-effort once-per-tenant seed of the brand playbook into the persistent
    document store, so the demo has a real active doc on first studio use. Guarded by
    an ``app.state`` set (marked before seeding to avoid a duplicate concurrent seed);
    the seed itself is idempotent regardless. Never raises into the request path."""
    try:
        seeded = getattr(app.state, "_docs_seeded", None)
        if seeded is None:
            seeded = set()
            app.state._docs_seeded = seeded
        if tenant_id in seeded:
            return
        seeded.add(tenant_id)
        from studio.documents import seed_tenant_documents

        seed_tenant_documents(tenant_id, dsn=dsn)
    except Exception:
        pass


def mount_studio_agui(app) -> None:
    """Mount ``POST /studio/agui`` alongside the existing /graphql + SSE."""
    from pydantic_ai.ui.ag_ui import AGUIAdapter

    from obsapi.db import get_dsn

    if getattr(app.state, "_studio_agui_mounted", False):
        return
    app.state._studio_agui_mounted = True
    # Let the chat host's run_campaign tool launch background runs on THIS app's
    # registry (the one GET /studio/run/{id} polls) instead of blocking its turn.
    global _MOUNTED_APP
    _MOUNTED_APP = app

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

        # Persist the operator's latest message as an 'operator' turn — ONLY when this
        # dispatch actually carries a NEW user turn (see _operator_turn_text: the
        # approval resume re-POSTs the same thread, which double-wrote the turn).
        last_user = _operator_turn_text(payload.get("messages"))
        if last_user:
            try:
                await asyncio.to_thread(_log_turn, dsn, session_id, "operator", last_user, None)
            except Exception:
                pass

        # Tenant the studio writes under (PENDING actions, materialized runs, assets).
        # Env-overridable so the booter can ALIGN it with the console's
        # NEXT_PUBLIC_TENANT_ID — otherwise studio output lands in a tenant the
        # Runs/Review tabs don't query. Default "demo" matches the existing data.
        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")
        # Seed the persistent brand doc on first use so the host genuinely has the
        # operator's documents to read this turn (best-effort, idempotent).
        await asyncio.to_thread(_ensure_docs_seeded, app, dsn, tenant_id)
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
                    await asyncio.to_thread(_persist_thinking, dsn, session_id, segments)
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
    try:  # live-state tools (item 4) read in-flight statuses from this registry
        from studio.live_state import set_runs_registry

        set_runs_registry(runs_registry)
    except Exception:
        pass

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
                    steps.append(
                        {
                            "seq": i,
                            "role": ar.get("role"),
                            "model": ar.get("model"),
                            "input": ar.get("input"),
                            "output": ar.get("output"),
                            "createdAt": ca.isoformat() if hasattr(ca, "isoformat") else str(ca),
                        }
                    )
                try:
                    row = c.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()
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
                        pending_actions.append(
                            {
                                "id": ar.get("id"),
                                "channel": ar.get("channel"),
                                "target": ar.get("target"),
                                "subject": ar.get("subject"),
                                "draft": draft_txt,
                                "idempotencyKey": ar.get("idempotency_key"),
                                "status": ar.get("status"),
                            }
                        )
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
            # Mid-run artwork pause (item 3): the durable selection row is the source
            # of truth — it survives an engine restart (the in-memory registry does
            # not), so a poller always sees the pending question + options.
            selection_request = None
            try:
                from studio.artwork_flow import get_selection, selection_request_payload

                sel = get_selection(run_id, dsn=dsn)
                if sel and sel.get("status") == "awaiting":
                    selection_request = selection_request_payload(sel)
                    if status in (None, "running", "awaiting_selection"):
                        status = "awaiting_selection"
            except Exception:
                selection_request = None
            if status is None:
                status = (
                    "completed"
                    if runs_status in ("completed", "success")
                    else ("running" if steps else "unknown")
                )
            # P1.5: read the executable plan from its dedicated ``campaign_blueprints`` row
            # (the authored plan), falling back to the planner agent_run.output; and compute
            # the progress board ON DEMAND from the loaded rows (NO board table). Both
            # honest-null on a pre-P1.5 run.
            blueprint = None
            try:
                from studio.blueprint_store import get_blueprint

                bp_row = get_blueprint(run_id, dsn=dsn)
                if bp_row and bp_row.get("state"):
                    blueprint = bp_row["state"]
            except Exception:
                blueprint = None
            if blueprint is None:
                for st in steps:
                    out = st.get("output")
                    if (
                        st.get("role") == "planner"
                        and isinstance(out, dict)
                        and out.get("blueprint")
                    ):
                        blueprint = out["blueprint"]
                        break
            board = None
            try:
                from types import SimpleNamespace

                from studio.progress_board import board_for_run

                quota = ((blueprint or {}).get("stop_conditions") or {}).get("total_quota") or 0
                chans = list(((blueprint or {}).get("per_channel_quota") or {}).keys())
                plan_ctx = SimpleNamespace(output_count=quota, lead_count=0, channels=chans)
                run_actions = [
                    SimpleNamespace(run_id=run_id, status=p.get("status")) for p in pending_actions
                ]
                # Reflect the REAL run status on the board (board_for_run passes record=None,
                # which would otherwise always read 'running' even for a completed run).
                board_record = SimpleNamespace(status=status) if status else None
                board = board_for_run(
                    run_id, board_record, steps, run_actions, plan_ctx
                ).model_dump()
            except Exception:
                board = None

            # CustomerAcq-6bv: the ONE real-state surface the voice supervisor answers
            # from — the SAME ordered draft rows the frontend renders, so "draft #1", the
            # count, and "did the strategist run" can never disagree between voice + FE.
            # Credit-independent (DB only). Reads ALL of the run's draft actions (every
            # status, full fields) so the count is truthful — not just the pending subset.
            try:
                from studio.campaign_state import campaign_state, describe_state

                voice_state = campaign_state(run_id, dsn=dsn, run_status=runs_status)
                voice_briefing = describe_state(voice_state)
            except Exception:
                voice_state, voice_briefing = None, None

            return {
                "status": status,
                "steps": steps,
                # Live host narration — one honest line per REAL recorded step.
                "narration": run_narration(steps),
                "nPending": n_pending,
                "pending": pending_actions,
                "archetype": archetype,
                "blueprint": blueprint,
                "board": board,
                "error": reg.get("error") if reg else None,
                # Mid-run artwork pause (item 3): non-null while the run awaits the
                # operator's pick — {"kind":"artwork","question":...,"options":[...]}.
                "selectionRequest": selection_request,
                # Real campaign state for the voice supervisor (draft #1, counts, agents).
                "state": voice_state,
                "voiceBriefing": voice_briefing,
            }

        data = await asyncio.to_thread(_load)
        return JSONResponse({"ok": True, "runId": run_id, **data})

    @app.post("/studio/campaign/{run_id}/select-artwork")
    async def studio_select_artwork_route(run_id: str, request: Request):  # noqa: ANN202
        """Answer a run's artwork selection pause (item 3). Body ``{"assetId": ...}``.

        Records the pick DURABLY (artwork_selections → 'selected') and RESUMES the
        run by re-invoking the executor with the same run id — the durable
        replay-skip + deterministic one-shot agent-run ids make the resume
        idempotent (nothing is re-drafted, nothing double-staged, nothing sent).
        400 for a pick outside the surfaced options; 404 when the run has no
        pending selection."""
        from fastapi.responses import JSONResponse

        from studio.artwork_flow import get_selection, record_choice

        dsn = get_dsn()
        try:
            payload = json.loads(await request.body() or b"{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        asset_id = (payload.get("assetId") or payload.get("asset_id") or "").strip()
        if not asset_id:
            return JSONResponse({"ok": False, "error": "missing assetId"}, status_code=400)

        sel = await asyncio.to_thread(lambda: get_selection(run_id, dsn=dsn))
        if sel is None or sel.get("status") != "awaiting":
            return JSONResponse(
                {"ok": False, "error": "no pending artwork selection for this run"},
                status_code=404,
            )
        options = {str(o.get("assetId")) for o in (sel.get("options") or [])}
        if asset_id not in options:
            return JSONResponse(
                {
                    "ok": False,
                    "error": f"assetId {asset_id!r} is not one of the surfaced options",
                    "options": sorted(options),
                },
                status_code=400,
            )
        artifact_id = next(
            (o.get("artifactId") for o in (sel.get("options") or [])
             if str(o.get("assetId")) == asset_id),
            None,
        )
        recorded = await asyncio.to_thread(
            lambda: record_choice(run_id, asset_id, artifact_id=artifact_id, dsn=dsn)
        )
        if not recorded:
            return JSONResponse(
                {"ok": False, "error": "selection was already recorded"}, status_code=409
            )

        # RESUME: re-invoke the executor with the plan snapshot the pause captured
        # (falls back to the session's current plan when no snapshot persisted).
        session_id = sel.get("session_id") or "studio-default"
        tenant_id = sel.get("tenant_id") or os.environ.get("STUDIO_TENANT_ID", "demo")
        plan_snapshot = sel.get("plan")
        if isinstance(plan_snapshot, dict) and plan_snapshot:
            try:
                plan = CampaignPlan.model_validate(plan_snapshot)
            except Exception:
                plan = _load_plan(session_id, dsn)
        else:
            plan = await asyncio.to_thread(_load_plan, session_id, dsn)
        start_registered_run(app, dsn, session_id, tenant_id, plan, run_id)
        return JSONResponse(
            {
                "ok": True,
                "runId": run_id,
                "assetId": asset_id,
                "artifactId": artifact_id,
                "status": "running",
                "note": "Artwork recorded; the run resumed with your pick. Everything "
                "stays HELD for approval — nothing is sent.",
            }
        )

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
            (
                payload.get("sessionId") or payload.get("threadId")
                if isinstance(payload, dict)
                else None
            )
            or request.query_params.get("session_id")
            or "studio-default"
        )
        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")

        # BRANCH on the upload's header shape. A competitor-posts CSV/JSON
        # (handle,url,platform,caption,likes,...,niche,posted_at) is COMPETITIVE
        # INTEL, not an audience: ingest into ``competitor_posts`` (idempotent on
        # tenant+url; missing metric columns stay absent, never zero-filled) and
        # return — competitors are never send targets, so no plan attach.
        from studio.competitor_intel import ingest_competitor_csv, looks_like_competitor_csv

        if looks_like_competitor_csv(content):
            try:
                ingest = await asyncio.to_thread(
                    lambda: ingest_competitor_csv(tenant_id, content, dsn=dsn)
                )
            except Exception as exc:  # honest: never claim the intel was stored
                return JSONResponse(
                    {"ok": False, "kind": "competitors",
                     "error": f"{type(exc).__name__}: {exc}"},
                    status_code=500,
                )
            return JSONResponse(
                {
                    "ok": True,
                    "kind": "competitors",
                    "filename": filename,
                    "rows": ingest.get("rows", 0),
                    "ingested": ingest.get("ingested", 0),
                    "duplicates": ingest.get("duplicates", 0),
                    "skipped": ingest.get("skipped", 0),
                    "handles": ingest.get("handles", []),
                    "note": "Competitor posts stored for creative intelligence — "
                    "the IG crew molds structure/hook/CTA patterns from the "
                    "best-scoring posts; artwork, wording, and offers stay ours. "
                    "Nothing was sent.",
                }
            )

        # CONVERSATION CSVs (speaker + text columns) take the reactivation intake:
        # verbatim threads land in lead_conversations, customers are upserted, and
        # the cohort attaches to the plan — same operator gesture, richer evidence.
        from studio.appointment_import import ingest_appointments_csv, is_appointment_csv
        from studio.conversation_import import ingest_conversations_csv, is_conversation_csv

        if is_conversation_csv(content):
            try:
                conv = await asyncio.to_thread(
                    lambda: ingest_conversations_csv(tenant_id, content, dsn=dsn)
                )
            except Exception as exc:
                return JSONResponse(
                    {"ok": False, "kind": "conversations",
                     "error": f"{type(exc).__name__}: {exc}"},
                    status_code=400,
                )
            # Attach the cohort so a provided-leads run targets EXACTLY these people
            # and the supervisor can read back real counts.
            try:

                def _attach_conversations() -> None:
                    plan = _load_plan(session_id, dsn)
                    plan.customers = {
                        "filename": filename,
                        "rows": int(conv.get("customers") or 0),
                        "columns": ["conversation transcript"],
                        "sample": list(conv.get("sample") or []),
                        "ingested": True,
                        "customer_ids": list(conv.get("customer_ids") or []),
                        "profile": {"kind": "conversations",
                                    "turns": int(conv.get("turns") or 0)},
                        "summary": (
                            f"{conv.get('customers')} customer conversation(s) imported "
                            f"({conv.get('turns')} verbatim messages); "
                            f"{len(conv.get('opted_out') or [])} lead(s) opted out of SMS"
                        ),
                    }
                    if conv.get("customers"):
                        plan.lead_count = int(conv["customers"])
                    _persist_plan(dsn, session_id, plan)

                await asyncio.to_thread(_attach_conversations)
                conv["attachedToPlan"] = True
            except Exception as exc:
                conv["attachedToPlan"] = False
                conv["attach_error"] = f"{type(exc).__name__}: {exc}"
            return JSONResponse({"ok": True, "kind": "conversations", **conv})

        # APPOINTMENT CSVs (appointment_id + identity + date columns) take the
        # booking-history intake: session days land in `appointments` keyed on
        # (tenant, appointment_id, slot date) so a re-upload never duplicates,
        # customers are upserted, and each gets ONE dossier-visible history memory.
        elif is_appointment_csv(content):
            try:
                appt = await asyncio.to_thread(
                    lambda: ingest_appointments_csv(
                        tenant_id, content, dsn=dsn, source_file=filename
                    )
                )
            except Exception as exc:
                return JSONResponse(
                    {"ok": False, "kind": "appointments",
                     "error": f"{type(exc).__name__}: {exc}"},
                    status_code=400,
                )
            # Attach the cohort so a provided-leads run targets EXACTLY these people
            # and the supervisor can read back real counts.
            try:

                def _attach_appointments() -> None:
                    plan = _load_plan(session_id, dsn)
                    perf = dict(appt.get("performance") or {})
                    span = perf.get("date_span") or {}
                    span_note = (
                        f", spanning {span.get('from')}..{span.get('to')}"
                        if span.get("from") else ""
                    )
                    plan.customers = {
                        "filename": filename,
                        "rows": int(appt.get("customers") or 0),
                        "columns": ["appointment history"],
                        "sample": list(appt.get("sample") or []),
                        "ingested": True,
                        "customer_ids": list(appt.get("customer_ids") or []),
                        "profile": {"kind": "appointments", **perf},
                        "summary": (
                            f"{appt.get('appointments')} appointment(s) "
                            f"({appt.get('sessions')} session day(s)) imported for "
                            f"{appt.get('customers')} customer(s){span_note}"
                        ),
                    }
                    if appt.get("customers"):
                        plan.lead_count = int(appt["customers"])
                    _persist_plan(dsn, session_id, plan)

                await asyncio.to_thread(_attach_appointments)
                appt["attachedToPlan"] = True
            except Exception as exc:
                appt["attachedToPlan"] = False
                appt["attach_error"] = f"{type(exc).__name__}: {exc}"
            return JSONResponse({"ok": True, "kind": "appointments", **appt})

        # (default branch) customers CSV → parse + ingest + attach + register.
        try:
            result = parse_customers_csv(content, filename)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        # INGEST: upsert parsed leads into ``customers`` (keyed on tenant+email) so
        # ``research_lead`` / ``research_and_stage_leads`` can find them. Idempotent —
        # re-uploading already-seeded leads matches them and creates no duplicates.
        # Honest: if ingestion fails we still return the parse preview with the error.
        try:
            from studio.customer_research import ingest_leads

            # Re-parse ALL data rows (parse_customers_csv samples only the first 5).
            import csv as _csv
            import io as _io

            reader = _csv.DictReader(_io.StringIO((content or "").lstrip("﻿")))
            rows = [{(k or "").strip(): (v or "") for k, v in r.items()} for r in reader]
            ingest = await asyncio.to_thread(lambda: ingest_leads(tenant_id, rows, dsn=dsn))
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
                    # P1-A: the semantic profile + honest natural summary of the file, so
                    # the supervisor can read back real counts (see `_customers_context`).
                    "profile": dict(result.get("profile") or {}),
                    "summary": result.get("summary") or "",
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

        # REGISTER the CSV as a universal context artifact (nmh.4) so the voice
        # supervisor and every agent can see "the customer CSV" alongside every other
        # uploaded file and answer "can you see the CSV / how many files" from real
        # state. Best-effort: a registry hiccup never fails the upload (the plan attach
        # above already made the list visible to the chat host).
        try:

            def _register_csv() -> None:
                import hashlib

                from studio.artifacts import register_artifact

                csv_name = result.get("filename") or filename
                # Deterministic id keyed on (tenant, filename) so re-uploading the same
                # CSV (a typo fix, a re-export) REFRESHES the one artifact rather than
                # piling up duplicates that would inflate the supervisor's file count.
                art_id = (
                    "art_csv_" + hashlib.sha1(f"{tenant_id}:{csv_name}".encode()).hexdigest()[:16]
                )
                # Bounded parsed_content — the full rows live in `customers`; the
                # artifact carries the header + a sample so an agent can see the shape
                # without the whole file entering per-turn context.
                header = ",".join(str(c) for c in (result.get("columns") or []))
                sample_lines = [str(r) for r in (result.get("sample") or [])[:20]]
                parsed = (header + "\n" + "\n".join(sample_lines)).strip()
                register_artifact(
                    tenant_id,
                    csv_name,
                    "csv",
                    media_type="text/csv",
                    summary=result.get("summary") or "",
                    parsed_content=parsed,
                    source="upload",
                    artifact_id=art_id,
                    meta={
                        "rows": int(result.get("rows") or 0),
                        "columns": list(result.get("columns") or []),
                        "ingested": bool(result.get("ingested")),
                    },
                    dsn=dsn,
                )

            if result.get("rows"):
                await asyncio.to_thread(_register_csv)
                result["registeredArtifact"] = True
        except Exception as exc:
            result["registeredArtifact"] = False
            result["artifact_error"] = f"{type(exc).__name__}: {exc}"
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

    @app.post("/studio/documents")
    async def studio_documents_upload_route(request: Request):  # noqa: ANN202
        """Upload a PERSISTENT tenant document (brand playbook / strategy / etc.).

        Tenant-scoped and durable — it SURVIVES sessions/runs (NOT tied to a chat
        session id), so every agent reads it from now on. REAL parse only: the text is
        stored + chunked for ts_rank retrieval; nothing is sent. Accepts JSON
        ``{name, content, kind?}``. 400 on empty content."""
        from fastapi.responses import JSONResponse

        from studio import documents as docstore

        dsn = get_dsn()
        try:
            payload = json.loads(await request.body() or b"{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")
        name = (payload.get("name") or payload.get("filename") or "").strip() or "Document"
        kind = (payload.get("kind") or "doc").strip() or "doc"
        content = (payload.get("content") or "").strip()
        if not content:
            return JSONResponse(
                {"ok": False, "error": "empty document — no text content"},
                status_code=400,
            )
        try:
            doc_id = await asyncio.to_thread(
                lambda: docstore.add_document(
                    tenant_id, name, content, kind=kind, source="upload", dsn=dsn
                )
            )
            info = await asyncio.to_thread(lambda: docstore.get_document(doc_id, dsn=dsn))
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status_code=500
            )

        # REGISTER the doc as a universal context artifact (nmh.4) so it shows up in
        # the one file inventory the voice supervisor + every agent read, alongside the
        # CSV and any images. A brand-voice doc (kind='brand') registers as a
        # 'brand_voice' artifact so "can you see the brand voice file?" answers YES from
        # real state. The artifact links back to the doc via meta['document_id'] (the
        # RAG chunks stay in tenant_documents; the artifact is the unified index).
        # Best-effort: a registry hiccup never fails the doc upload.
        artifact_type = (
            "brand_voice" if kind == "brand" else ("pdf" if kind == "pdf" else "document")
        )
        try:
            await asyncio.to_thread(
                lambda: _register_document_artifact(
                    tenant_id,
                    name,
                    artifact_type,
                    content,
                    summary=(info or {}).get("summary"),
                    document_id=doc_id,
                    dsn=dsn,
                )
            )
        except Exception:
            pass  # the doc is stored; the artifact index is a best-effort convenience
        return JSONResponse(
            {
                "ok": True,
                "id": doc_id,
                "name": name,
                "kind": kind,
                "chars": (info or {}).get("chars", len(content)),
                "chunks": (info or {}).get("chunks", 0),
                "note": "Document stored persistently — the whole team (host, run "
                "agents, voice) reads it from now on. Nothing was sent.",
            }
        )

    @app.post("/studio/upload/image")
    async def studio_upload_image_route(request: Request):  # noqa: ANN202
        """Upload an IMAGE / artwork / screenshot: disk bytes + REAL VLM understanding.

        Accepts JSON ``{name, contentBase64, mediaType?, kind?, artist?, prompt?,
        linkedArtistId?}`` (``artist`` = name or slug; ``prompt`` = the operator's
        text about the design). The pipeline (studio/image_ingest.py) writes the
        bytes to ``var/artifacts/{tenant}/{sha256}.{ext}``, runs REAL VLM analysis
        (tattoo style / motif / color-vs-black-and-grey / mood / complexity /
        campaign-fit — image-level facts), registers the artifact (metadata +
        storage path + a bounded <=64k thumbnail), adds an artwork LIBRARY row so
        campaign artwork selection can pick it, and records an artist memory.
        HONEST DEGRADATION: with no model key / a VLM failure the image + artist +
        prompt still persist and ``vlmStatus='unavailable'`` says why — tags are
        never fabricated. Nothing is sent."""
        import base64

        from fastapi.responses import JSONResponse

        dsn = get_dsn()
        try:
            payload = json.loads(await request.body() or b"{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")
        name = (payload.get("name") or payload.get("filename") or "").strip()
        b64 = payload.get("contentBase64") or payload.get("content") or ""
        media_type = (payload.get("mediaType") or payload.get("media_type") or "").strip() or None
        kind = (payload.get("kind") or "image").strip().lower()
        artist = (
            payload.get("artist") or payload.get("linkedArtistId") or ""
        ).strip() or None
        prompt = (payload.get("prompt") or "").strip() or None
        if not name or not b64:
            return JSONResponse(
                {"ok": False, "error": "image upload needs a name and contentBase64"},
                status_code=400,
            )
        # Accept either a raw base64 payload or a full data-URI: strip a leading
        # `data:<media>;base64,` prefix (and adopt its media type when none was given)
        # so a data-URI upload does not decode to garbage / double-prefix the preview.
        if b64.startswith("data:"):
            head, _, rest = b64.partition(",")
            if rest:
                if media_type is None and ";" in head:
                    media_type = head[len("data:") : head.index(";")] or media_type
                b64 = rest
        # Decode only to measure real bytes (never fabricate a size); reject non-image.
        try:
            raw = base64.b64decode(b64, validate=False)
        except Exception:
            return JSONResponse(
                {"ok": False, "error": "contentBase64 is not valid base64"}, status_code=400
            )
        if not raw:
            return JSONResponse(
                {"ok": False, "error": "contentBase64 decoded to zero bytes"},
                status_code=400,
            )
        # Videos take the frame-sampled pipeline (studio/video_ingest.py): same
        # disk + artifact + b-roll library + memory writes, VLM over REAL frames.
        is_video = bool(media_type and media_type.startswith("video/")) or (
            not media_type
            and name.lower().endswith((".mp4", ".mov", ".webm", ".m4v", ".avi"))
        )
        if media_type and not media_type.startswith(("image/", "video/")):
            return JSONResponse(
                {"ok": False, "error": f"mediaType {media_type!r} is not image/* or video/*"},
                status_code=400,
            )
        try:
            if is_video:
                from studio.video_ingest import process_video_upload

                result = await asyncio.to_thread(
                    lambda: process_video_upload(
                        tenant_id,
                        name,
                        raw,
                        media_type=media_type or "video/mp4",
                        artist=artist,
                        prompt=prompt,
                        dsn=dsn,
                    )
                )
            else:
                from studio.image_ingest import process_image_upload

                result = await asyncio.to_thread(
                    lambda: process_image_upload(
                        tenant_id,
                        name,
                        raw,
                        media_type=media_type,
                        kind=kind,
                        artist=artist,
                        prompt=prompt,
                        dsn=dsn,
                    )
                )
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status_code=500
            )
        return JSONResponse(result)

    @app.get("/studio/documents")
    async def studio_documents_list_route():  # noqa: ANN202
        """List this tenant's ACTIVE persistent documents (the compact index the
        agents read). Best-effort seeds the brand playbook on first use so the demo
        has a real doc immediately. Honest-empty when none."""
        from fastapi.responses import JSONResponse

        from studio import documents as docstore

        dsn = get_dsn()
        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")
        await asyncio.to_thread(_ensure_docs_seeded, app, dsn, tenant_id)
        try:
            docs = await asyncio.to_thread(lambda: docstore.active_docs_index(tenant_id, dsn=dsn))
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}", "documents": []},
                status_code=500,
            )
        return JSONResponse(
            {
                "ok": True,
                "documents": [
                    {
                        "id": d.get("id"),
                        "name": d.get("name"),
                        "kind": d.get("kind"),
                        "summary": d.get("summary"),
                        "chars": d.get("chars"),
                        "chunks": d.get("chunks"),
                        "source": d.get("source"),
                        "createdAt": (
                            d["created_at"].isoformat()
                            if hasattr(d.get("created_at"), "isoformat")
                            else str(d.get("created_at"))
                        ),
                    }
                    for d in docs
                ],
            }
        )

    @app.post("/studio/documents/remove")
    async def studio_documents_remove_route(request: Request):  # noqa: ANN202
        """Soft-remove a document (``active=false``) so it drops from EVERY agent
        surface at once. Body ``{id}``. Returns whether an active doc was removed
        (real-only — never a fake success)."""
        from fastapi.responses import JSONResponse

        from studio import documents as docstore

        dsn = get_dsn()
        try:
            payload = json.loads(await request.body() or b"{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")
        doc_id = (payload.get("id") or payload.get("documentId") or "").strip()
        if not doc_id:
            return JSONResponse({"ok": False, "error": "missing document id"}, status_code=400)
        try:
            removed = await asyncio.to_thread(
                lambda: docstore.deactivate_document(tenant_id, doc_id, dsn=dsn)
            )
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status_code=500
            )
        return JSONResponse({"ok": True, "id": doc_id, "removed": removed})

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
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)
        if ev is None:
            return JSONResponse({"error": "no such action"}, status_code=404)
        payload = ev.model_dump(by_alias=True)
        # Engine-core item 7 (additive): ARTWORK + ARTIST + CUSTOMER-DOSSIER links,
        # read off the action's own context (the run wrote them) — real-only, each
        # key present ONLY when the underlying fact exists (never a fabricated link).
        try:
            payload.update(await asyncio.to_thread(_evidence_links, action_id, dsn))
        except Exception:
            pass
        return JSONResponse(payload)

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

    @app.post("/studio/send-eligible")
    async def studio_send_all_eligible_route(request: Request):  # noqa: ANN202
        """THE one-button send: every eligible/safe PENDING draft of the ACTIVE TENANT
        (all campaigns), each through the existing per-draft ``approve_and_publish``
        (atomic exactly-once claim + tenant TEST-MODE gate + allow-list/redirect) —
        NOT a bulk bypass. Non-eligible drafts come back under ``skipped``; ``live``
        must be explicitly true to lift the redirect, exactly like the per-run route."""
        from fastapi.responses import JSONResponse

        from studio.campaign_send import send_eligible

        try:
            payload = json.loads(await request.body() or b"{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")
        live = payload.get("live") is True
        dsn = get_dsn()
        try:
            out = await asyncio.to_thread(
                send_eligible,
                tenant_id=tenant_id,
                dsn=dsn,
                operator=payload.get("operator"),
                live=live,
            )
        except Exception as exc:
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)
        return JSONResponse(out)

    @app.post("/studio/run/{run_id}/steer")
    async def studio_run_steer_route(run_id: str, request: Request):  # noqa: ANN202
        """SUPERVISOR FULL-DUPLEX: queue a steering directive for a live run —
        pause / abort / set_angle / set_offer / skip_lead / guide_copy. The executor
        consumes it at the next safe boundary (before the next lead) and records the
        application as a role='supervisor' agent_run. Directives can only narrow or
        redirect a run; there is no kind that widens delivery or lifts a gate."""
        from fastapi.responses import JSONResponse

        from studio.supervisor_control import issue_directive

        try:
            payload = json.loads(await request.body() or b"{}")
        except Exception:
            payload = {}
        kind = str((payload or {}).get("kind") or "").strip()
        directive_payload = (payload or {}).get("payload") or {}
        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")
        try:
            row = await asyncio.to_thread(
                issue_directive,
                run_id,
                tenant_id,
                kind,
                directive_payload if isinstance(directive_payload, dict) else {},
                issued_by=str((payload or {}).get("issuedBy") or "operator"),
                dsn=get_dsn(),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)
        return JSONResponse({"ok": True, "directive": row})

    @app.get("/studio/run/{run_id}/directives")
    async def studio_run_directives_route(run_id: str):  # noqa: ANN202
        """Every directive issued for this run, with applied/pending status + notes."""
        from fastapi.responses import JSONResponse

        from studio.supervisor_control import list_directives

        rows = await asyncio.to_thread(list_directives, run_id, dsn=get_dsn())
        return JSONResponse({"runId": run_id, "directives": rows})

    @app.post("/studio/run/{run_id}/review")
    async def studio_run_review_route(run_id: str):  # noqa: ANN202
        """The supervisor's coherence audit over this run's REAL recorded agent
        outputs (researcher vs strategist vs analyst): deterministic contradiction
        rules + one policy-clamped LLM read when configured. Returns the honest
        verdict + suggested directives; applies nothing by itself."""
        from fastapi.responses import JSONResponse

        from studio.supervisor_control import review_run_coherence

        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")
        verdict = await asyncio.to_thread(
            review_run_coherence, run_id, tenant_id, dsn=get_dsn()
        )
        return JSONResponse(verdict)

    @app.get("/studio/sessions")
    async def studio_sessions_route():  # noqa: ANN202
        """Conversation session list (chat-app style): every session (from the shared
        ``studio_chat_turns`` transcript store) with its first operator line as the
        title, turn count, and last activity — newest-active first. Honest empty
        list when none exist."""
        from fastapi.responses import JSONResponse

        def _list() -> list[dict[str, Any]]:
            import psycopg
            from psycopg.rows import dict_row

            with psycopg.connect(get_dsn(), row_factory=dict_row, connect_timeout=5) as conn:
                rows = conn.execute(
                    """
                    SELECT session_id,
                           count(*)            AS turns,
                           max(created_at)     AS last_at,
                           min(created_at)     AS first_at,
                           (SELECT t2.text FROM studio_chat_turns t2
                             WHERE t2.session_id = t.session_id AND t2.role = 'operator'
                             ORDER BY t2.seq LIMIT 1) AS title
                      FROM studio_chat_turns t
                     GROUP BY session_id
                     ORDER BY max(created_at) DESC
                     LIMIT 30
                    """
                ).fetchall()
            return [
                {
                    "sessionId": r["session_id"],
                    "title": (r["title"] or "(no operator turn yet)")[:90],
                    "turns": int(r["turns"]),
                    "lastAt": r["last_at"].isoformat() if r["last_at"] else None,
                    "firstAt": r["first_at"].isoformat() if r["first_at"] else None,
                }
                for r in rows
            ]

        try:
            sessions = await asyncio.to_thread(_list)
        except Exception as exc:
            return JSONResponse({"sessions": [], "error": f"{type(exc).__name__}: {exc}"})
        return JSONResponse({"sessions": sessions})

    @app.get("/studio/sessions/{session_id}/turns")
    async def studio_session_turns_route(session_id: str):  # noqa: ANN202
        """Hydrate one session's full transcript (typed AND persisted spoken turns,
        in seq order) so switching sessions restores its real conversation."""
        from fastapi.responses import JSONResponse

        try:
            turns = await asyncio.to_thread(
                lambda: _chat_store(get_dsn()).history(session_id)
            )
        except Exception as exc:
            return JSONResponse(
                {"sessionId": session_id, "turns": [], "error": f"{type(exc).__name__}: {exc}"}
            )
        return JSONResponse(
            {
                "sessionId": session_id,
                "turns": [
                    {
                        "id": t.id,
                        "role": t.role,
                        "text": t.text,
                        "model": t.model,
                        "at": t.created_at if isinstance(t.created_at, str) else (
                            t.created_at.isoformat() if t.created_at else None
                        ),
                    }
                    for t in turns
                ],
            }
        )

    @app.get("/studio/intelligence")
    async def studio_intelligence_route():  # noqa: ANN202
        """The executive brain: best real campaigns, extracted patterns, artist
        library depth, the objection landscape read from real analyst steps, the
        queue state, competitor leaders, and rule-based recommendations — each
        carrying its evidence. Read-only, deterministic, honest-empty sections."""
        from fastapi.responses import JSONResponse

        from studio.intelligence import campaign_intelligence

        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")
        out = await asyncio.to_thread(campaign_intelligence, tenant_id, dsn=get_dsn())
        return JSONResponse(out)

    @app.get("/studio/fleet")
    async def studio_fleet_route():  # noqa: ANN202
        """`initech status` for the marketing agents: one row per recent run with
        live activity classification (working / stalled / waiting-operator / done /
        failed), current role, last-step age, staged drafts and directive counts —
        every field read from the runs/agent_runs/actions/run_directives tables."""
        from fastapi.responses import JSONResponse

        from studio.supervisor_fleet import fleet_status

        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")
        board = await asyncio.to_thread(fleet_status, tenant_id, dsn=get_dsn())
        return JSONResponse({"tenantId": tenant_id, "fleet": board})

    @app.get("/studio/social/ready")
    async def studio_social_ready_route():  # noqa: ANN202
        """Social Ready Queue: every PENDING instagram/facebook draft as a full
        post package — caption, target, schedule, and the media resolved from the
        REAL asset rows its context references (artwork tags + image/video kind,
        optional b-roll) — plus the honest publish-gate state: publishable=false
        with the exact blocked_reason while the operator's Meta credentials are
        absent (the same reason an approve would refuse with). Read-only,
        honest-empty list when nothing is pending."""
        from fastapi.responses import JSONResponse

        from studio.social_queue import ready_posts

        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")
        try:
            posts = await asyncio.to_thread(ready_posts, tenant_id, dsn=get_dsn())
        except Exception as exc:
            return JSONResponse(
                {"tenantId": tenant_id, "posts": [],
                 "error": f"{type(exc).__name__}: {exc}"}
            )
        return JSONResponse({"tenantId": tenant_id, "posts": posts})

    @app.post("/studio/fleet/patrol")
    async def studio_fleet_patrol_route():  # noqa: ANN202
        """`initech patrol` on demand: one sweep over every non-terminal run —
        stall detection + deterministic coherence rules; each NEW finding is
        recorded as a role='supervisor' agent_run (deduped per run+rule). The
        background loop runs this automatically; this route is the manual kick."""
        from fastapi.responses import JSONResponse

        from studio.supervisor_fleet import patrol_once

        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")
        summary = await asyncio.to_thread(patrol_once, tenant_id, dsn=get_dsn())
        return JSONResponse(summary)

    @app.on_event("startup")
    async def _start_supervisor_patrol():  # noqa: ANN202
        """The supervisor's continuous loop (initech pattern): patrol every
        SUPERVISOR_PATROL_SECONDS (default 60; 0 disables). Observation-only —
        corrections still go through the closed directive set."""
        from studio.supervisor_fleet import start_patrol_loop

        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")
        app.state._supervisor_patrol_task = asyncio.create_task(
            start_patrol_loop(tenant_id, dsn=get_dsn())
        )

    @app.on_event("startup")
    async def _start_action_scheduler():  # noqa: ANN202
        """Deferred-publish loop: operator-scheduled drafts publish at their time
        through the REAL approve path (every gate intact). ACTION_SCHEDULER_SECONDS
        (default 60; 0 disables)."""
        from studio.scheduler import start_scheduler_loop

        app.state._action_scheduler_task = asyncio.create_task(
            start_scheduler_loop(dsn=get_dsn())
        )

    @app.post("/studio/campaign/action/{action_id}/schedule")
    async def studio_action_schedule_route(action_id: str, request: Request):  # noqa: ANN202
        """OPERATOR-INITIATED deferred publish: schedule ONE pending draft for a
        future time. This is an approval gesture with a timestamp — the scheduler
        publishes through approve_and_publish, so the exactly-once claim, the
        tenant TEST-MODE gate and the allow-list/redirect all still apply. ``live``
        must be explicitly true to request a non-redirect send at publish time."""
        from fastapi.responses import JSONResponse

        from studio.scheduler import cancel_schedule, schedule_action

        try:
            payload = json.loads(await request.body() or b"{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if payload.get("cancel") is True:
            cleared = await asyncio.to_thread(cancel_schedule, action_id, dsn=get_dsn())
            return JSONResponse({"ok": True, "cancelled": bool(cleared)})
        when = str(payload.get("when") or "").strip()
        if not when:
            return JSONResponse(
                {"ok": False, "error": "body needs {'when': RFC3339 timestamp}"},
                status_code=400,
            )
        try:
            out = await asyncio.to_thread(
                schedule_action, action_id, when,
                live=payload.get("live") is True, dsn=get_dsn(),
            )
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, **out})

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
        if not isinstance(payload, dict):
            payload = {}
        operator = payload.get("operator")
        # EXPLICIT operator live-send authorization. Default False = safe redirect; a
        # campaign send is live ONLY when the operator flipped the toggle to Live. Any
        # non-true value (absent / null / false) stays redirect.
        live = payload.get("live") is True
        dsn = get_dsn()
        try:
            out = await asyncio.to_thread(
                send_eligible, run_id=run_id, dsn=dsn, operator=operator, live=live
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
        # EXPLICIT operator live-send authorization (default False = safe redirect). An
        # override bypasses the confidence bar, NOT the send-path redirect default.
        live = payload.get("live") is True
        if not reason:
            return JSONResponse(
                {"ok": False, "error": "override requires an explicit reason"},
                status_code=400,
            )
        dsn = get_dsn()
        try:
            out = await asyncio.to_thread(
                override_send, action_id, reason=reason, operator=operator, dsn=dsn, live=live
            )
        except OverrideRequiresReasonError:
            return JSONResponse(
                {"ok": False, "error": "override requires an explicit reason"},
                status_code=400,
            )
        except Exception as exc:
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)
        return JSONResponse({"ok": True, **out})

    @app.post("/studio/customers/{customer_id}/enrich")
    async def studio_customer_enrich_route(customer_id: str):  # noqa: ANN202
        """OPERATOR-INITIATED evidence-cited lead enrichment (never auto-run in any
        loop — per-lead live egress stays a deliberate human decision): 1–3 cited
        public-web lookups through the shared research seam, sensitive-trait
        post-filter applied, and ONE replaceable customer memory written only when
        a cited fact survives. Returns the honest result JSON — ``found`` facts
        each with url+quote, ``misses`` per query, the ``suppressed`` count, and
        ``memory_id`` (None on an honest miss). 404 for an unknown customer."""
        from fastapi.responses import JSONResponse

        from studio.lead_enrichment import enrich_lead

        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")
        dsn = get_dsn()
        try:
            out = await asyncio.to_thread(enrich_lead, tenant_id, customer_id, dsn=dsn)
        except LookupError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except Exception as exc:
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)
        return JSONResponse(out)
