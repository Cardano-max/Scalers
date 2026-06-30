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
    "4. NEVER send or publish anything yourself. If the operator wants something "
    "posted/emailed, call `stage_publish` — it stages a PENDING action that a "
    "human must approve; it is held, never sent.\n"
    "5. After acting, reply in 2-4 sentences: reflect the current plan and ask 1 "
    "high-leverage clarifying question. Be honest — never claim a tool ran that "
    "did not, and never claim anything was sent."
)


# ``output_type`` MUST include ``DeferredToolRequests`` because this agent owns an
# approval-gated tool (``stage_publish``, ``requires_approval=True``). pydantic-ai's
# ``AGUIAdapter.run_stream_native`` only appends ``DeferredToolRequests`` automatically
# when the inbound request carries frontend tools; an AG-UI client that sends no
# frontend tools would otherwise hit "A deferred tool call was present, but
# `DeferredToolRequests` is not among output types" and the approval gate would 500
# instead of surfacing an Approve/Reject interrupt. Declaring it here makes the gate
# work unconditionally (and matches what the hermetic approval test asserts).
studio_agent = Agent(
    HOST_AGUI_MODEL,
    deps_type=StudioDeps,
    output_type=[str, DeferredToolRequests],
    instructions=_SYSTEM,
    model_settings={"temperature": 0.4},
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
    return (
        "CURRENT CAMPAIGN PLAN — this is the live SHARED STATE, already reflecting any "
        "edits the operator just made to the plan fields on the frontend. Treat it as "
        "ground truth and re-plan around it; never claim you cannot see it:\n"
        f"- goal: {p.goal or '(empty)'}\n"
        f"- audience: {p.audience or '(empty)'}\n"
        f"- channels: {channels}\n"
        f"- sections: {sections}"
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
    return (
        f"Goal: {plan.goal or 'grow bookings'}\n"
        f"Audience: {plan.audience or 'local clients seeking custom tattoos'}\n"
        f"Channels: {', '.join(plan.channels) or 'instagram, email'}"
        + (f"\nSections: {', '.join(plan.sections)}" if plan.sections else "")
    )


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


def _execute_campaign_sync(
    plan: CampaignPlan, session_id: str, tenant_id: str, dsn: str | None
) -> dict[str, Any]:
    """SYNC: run the real traced Phase-A campaign for ``plan`` and persist its visible
    surfaces — mirror each per-role trace into the thread as a LABELED turn (so the
    operator can watch what each agent thought) and reflect the work into the shared
    plan. Returns the runner summary. NOTHING is sent (HELD/PENDING only)."""
    from studio.campaign_runner import run_and_trace

    summary = run_and_trace(brief=_brief_from_plan(plan), tenant_id=tenant_id, dsn=dsn)
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
    return summary


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

    @app.post("/studio/run")
    async def studio_run_route(request: Request):  # noqa: ANN202
        """DETERMINISTIC campaign run — the 'Run campaign' BUTTON path.

        Bypasses the Haiku host's free-text decision entirely: loads the session's
        persisted plan (optionally merged with an inline override the button sends so
        the run matches exactly what the operator sees) and runs the real traced
        Phase-A spine directly via the SHARED ``_execute_campaign_sync``. Logs an
        explicit operator trigger turn + the per-agent traces + a host summary so the
        thread reads naturally after the frontend refreshes history. NOTHING is sent —
        every output is HELD/PENDING behind approve-first (publishing still requires
        the separate stage_publish approval gate)."""
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

        try:
            await asyncio.to_thread(
                _log_turn, dsn, session_id, "operator", "Run the campaign now.", None
            )
        except Exception:
            pass

        try:
            summary = await asyncio.to_thread(
                _execute_campaign_sync, plan, session_id, tenant_id, dsn
            )
        except Exception as exc:  # honest failure — never a fake success
            return JSONResponse(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status_code=500
            )

        host_text = _summary_text(summary)
        try:
            await asyncio.to_thread(
                _log_turn, dsn, session_id, "host", host_text, HOST_AGUI_MODEL
            )
        except Exception:
            pass

        return JSONResponse(
            {
                "ok": True,
                "runId": summary.get("run_id"),
                "archetypeId": summary.get("archetype_id"),
                "nPending": summary.get("n_pending"),
                "nQueued": summary.get("n_queued"),
                "channels": summary.get("channels"),
                "runsRow": summary.get("runs_row"),
                "hostText": host_text,
            }
        )
