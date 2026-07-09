"""Speech-to-speech voice layer for the Campaign Studio (OpenAI Realtime, option B).

The voice agent is a pure FRONT-END (ears + mouth + interviewer). ALL campaign
reasoning stays on Claude / pydantic-ai. This module is the SERVER side of three
seams, and it is the only place the realtime posture is enforced:

  1. ``POST /studio/voice/session`` — mints a short-TTL **ephemeral** Realtime
     client secret with ``OPENAI_API_KEY`` (server-side only). The browser receives
     ONLY that ``ek_...`` secret, never the raw key. The minted session declares
     EXACTLY TWO tools (``update_plan`` + ``request_orchestration``) and NO
     send/publish tool — so the model is structurally incapable of sending.

  2. ``POST /studio/voice/plan`` — the server handler for the model's
     ``update_plan`` tool call. Persists the edited plan via the SAME ``_persist_plan``
     seam / ``sessionId`` the run uses. Computes (server-side) whether the plan is
     readback-ready and therefore whether the GO-gate may arm.

  3. ``POST /studio/voice/orchestrate`` — the server handler for the model's
     ``request_orchestration`` tool call, guarded by a SERVER-SIDE 2-factor GO-gate
     (NOT model-trusted). It launches the EXISTING held ``POST /studio/run`` spine
     (``launch_studio_run``) ONLY when both factors hold; otherwise it refuses and
     the model treats the utterance as an edit / keeps interviewing. NOTHING is sent
     on any path — every output stays PENDING / HELD behind the separate Review-Queue
     approve.

Tool calls are handled HERE (server), not in the model, so the GO-gate guard can
never be talked past by the voice agent. The browser is a thin relay: it forwards
the model's tool-call arguments to these routes and feeds the JSON result back as a
``function_call_output``.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from fastapi import Request

from studio.agui import (
    CampaignPlan,
    _load_plan,
    _persist_plan,
    launch_studio_run,
)

# --------------------------------------------------------------------------- #
# Realtime model + voice pins
# --------------------------------------------------------------------------- #

REALTIME_MODEL = os.environ.get("OPENAI_REALTIME_MODEL", "gpt-realtime")
# Premium MALE marketing-executive register. "cedar" is a real gpt-realtime voice
# (Realtime-API-exclusive, OpenAI-recommended for best quality) that reads as a
# confident, senior, calm male — the executive tone the operator asked for, replacing
# the light/female "marin". Overridable via OPENAI_REALTIME_VOICE. Other valid
# confident-male Realtime voices if a swap is ever wanted: ash, verse, ballad, echo.
REALTIME_VOICE = os.environ.get("OPENAI_REALTIME_VOICE", "cedar")
_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"

# --------------------------------------------------------------------------- #
# The EXACTLY-TWO tool surface exposed to the voice agent.
#
# This list is the single source of truth for what the realtime model can call.
# It contains update_plan + request_orchestration and NOTHING ELSE — there is no
# publish/send/stage tool anywhere in it, so the voice agent is structurally
# incapable of sending or publishing. (Defense in depth: the browser relay only
# has handlers for these two names, and the server only exposes the two matching
# routes, so even a hallucinated tool name has no send path.)
# --------------------------------------------------------------------------- #

VOICE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "update_plan",
        "description": (
            "Persist an edit to the SHARED campaign plan during the scoping "
            "interview. Call this whenever the operator states or changes the goal, "
            "audience, channels, sections, or schedule. Pass ONLY the fields that "
            "changed. This NEVER launches anything — it only edits the plan."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The campaign objective."},
                "audience": {"type": "string", "description": "Who the campaign targets."},
                "channels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Channels, e.g. instagram, email.",
                },
                "sections": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "schedule": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "request_orchestration",
        "description": (
            "Ask the SERVER to launch the held multi-agent campaign run. Only call "
            "this AFTER you have read the full plan back to the operator and they "
            "have answered your 'should I run this?' with an explicit go (go / run "
            "it / let's go / do it / kick it off). The server enforces a 2-factor "
            "gate and will REFUSE if the plan is not readback-ready or the utterance "
            "is an edit rather than a launch — that refusal is normal, keep "
            "interviewing. This launches a HELD run; nothing is ever sent."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]

# Names of the only tools the voice agent may call — used to assert the surface.
VOICE_TOOL_NAMES: tuple[str, ...] = tuple(t["name"] for t in VOICE_TOOLS)

VOICE_INSTRUCTIONS = (
    "You are the senior marketing-agency executive who hosts this Campaign Studio "
    "for a studio owner. Your register is that of a seasoned agency principal: "
    "confident, senior, calm, and premium — decisive and economical with words, "
    "warm but not chatty. You are NOT childish, bubbly, overly-friendly, robotic, "
    "or a light 'flight-attendant' greeter; you speak with the assured authority of "
    "someone who has run hundreds of campaigns. Carry the conversation like a "
    "trusted advisor: lead, do not hover.\n\n"
    "Despite that authority, your ROLE here is strictly EARS, MOUTH, and INTERVIEWER. "
    "You do NOT reason about the campaign content yourself and you CANNOT send or "
    "publish anything. The real multi-agent marketing team — research, strategy, "
    "copy, critique, jury — is the brain; it runs on the server when you request "
    "orchestration. You interview to scope the brief, then hand off to that team and "
    "narrate their real work.\n\n"
    "Follow this state machine, speaking with senior, unhurried confidence:\n"
    "GREET: welcome the operator as their strategist and ask, briefly, what campaign "
    "they want to run.\n"
    "INTERVIEW: ask short questions to capture the goal, the audience, and the "
    "channels (and optionally sections / schedule). Each time you learn or the "
    "operator changes a field, call update_plan with ONLY the changed fields. "
    "Treat 'go ahead and add X', 'also include Y', 'change it to Z' as EDITS — "
    "call update_plan, never request_orchestration.\n"
    "PLAN READBACK: once you have at least goal, audience, and channels, read the "
    "whole plan back out loud in one or two sentences, then ask: 'Should I run "
    "this?'\n"
    "GO-GATE: only if the operator answers with an explicit launch word (go / run "
    "it / let's go / do it / kick it off) do you call request_orchestration. If the "
    "server refuses, tell the operator what is still needed and keep interviewing.\n"
    "ORCHESTRATE: after a successful launch, narrate each agent's result as the run "
    "progresses. Make clear everything is HELD for approval and nothing was sent.\n\n"
    "ROUTE BY CHANNEL — run the workflow the operator ASKED for, not always email. "
    "'Send emails' runs the email pipeline; 'create an Instagram post' runs the "
    "Instagram pipeline; 'run a campaign for this artist with attachments' is the "
    "artist/artwork pipeline; 'Facebook campaign' is the Facebook pipeline. The server "
    "picks the pipeline from the plan and tells you which ran — narrate THAT channel. "
    "Some pipelines are not built yet (Facebook, artist/artwork attachments): if the "
    "server returns a not-built response, tell the operator honestly that that pipeline "
    "isn't built yet and offer email or Instagram instead — NEVER pretend a run "
    "happened or that you ran a different channel than the one requested.\n\n"
    "REAL STATE ONLY — never guess about the run. When the operator asks how many "
    "drafts exist, which agent is working, whether the strategist / researcher / critic "
    "/ jury ran, what draft #1 (or #N) says and WHO it is to, or why it was written, "
    "answer ONLY from the real run-state briefing you are given (the STATE section / the "
    "run-state surface). NEVER invent a lead name, a count, or a status. Draft #1 is the "
    "FIRST lead in that ordered list — say that exact name so it matches what the "
    "operator sees on screen. If a required step (e.g. the strategist or critic) is "
    "reported as failed, say so honestly — do not claim the run finished cleanly. If you "
    "do not have the state yet, say you are pulling it up rather than guessing.\n\n"
    "Never claim to have sent, posted, emailed, or published anything — you cannot."
)


# --------------------------------------------------------------------------- #
# Ephemeral-secret mint (raw OPENAI_API_KEY stays server-side)
# --------------------------------------------------------------------------- #


def voice_instructions_with_docs(
    tenant_id: str, *, dsn: str | None = None, base: str = VOICE_INSTRUCTIONS
) -> str:
    """The voice supervisor's instructions, with the ACTIVE persistent documents
    injected so it truthfully knows it HAS the operator's docs and can reference/reason
    over them by voice ("yes, I have your brand playbook"). Read-only: the voice agent
    gains NO tool from this — it stays structurally send-incapable (exactly two tools).

    HONESTY: with no active docs it is told plainly to say none are uploaded; the store
    being unreachable degrades to the base instructions, never a false claim. The honest
    DATA INVENTORY (ju1.3) is appended from the SAME shared builder the chat host uses, so
    voice and chat state the identical real counts + missing-data sentence (no divergence)."""
    inventory = _data_inventory_block(tenant_id, dsn=dsn)
    try:
        from studio.documents import active_docs_index

        docs = active_docs_index(tenant_id, dsn=dsn)
    except Exception:
        return base + inventory
    if not docs:
        return base + (
            "\n\nKNOWLEDGE: you currently have NO uploaded documents for this studio. "
            "If the operator asks whether you have their documents / brand playbook, say "
            "honestly that none are uploaded yet — never claim to have one you do not."
        ) + inventory
    lines = [
        base,
        "",
        "KNOWLEDGE — the operator has uploaded these persistent documents and the whole "
        "team (including you) reads them. When the operator asks 'do you have my "
        "documents / brand playbook?', answer YES and name them; you may reference and "
        "reason over them by voice (the drafting team grounds the actual copy in them):",
    ]
    for doc in docs:
        summ = (doc.get("summary") or "").strip()
        lines.append(f"- {doc.get('name')}" + (f": {summ}" if summ else ""))
    lines.append(
        "You still cannot send or publish anything — but you DO have and use these "
        "documents."
    )
    return "\n".join(lines) + inventory


def _data_inventory_block(tenant_id: str, *, dsn: str | None = None) -> str:
    """The honest data-inventory readback (ju1.3) as a voice-instruction block, from the
    ONE shared builder the chat host also calls — so both surfaces quote the identical
    real DB counts + missing-data sentence. Best-effort: unreadable store adds nothing."""
    try:
        from studio.interview import campaign_interview_prompt
        from studio.inventory import build_data_inventory

        readback = build_data_inventory(tenant_id, dsn=dsn)
    except Exception:
        return ""
    if not readback:
        return ""
    return "\n\n" + readback + "\n\n" + campaign_interview_prompt()


def voice_state_briefing(run_id: str, *, dsn: str | None = None) -> str:
    """A spoken, TRUTHFUL real-state briefing for the voice supervisor to narrate — the
    draft count, which agents ran (incl. an honest 'failed'), and draft #1 (the REAL
    first lead, matching the frontend). Credit-INDEPENDENT: reads the DB only, no model /
    ANTHROPIC key. This is what the supervisor answers 'how many drafts / did the
    strategist run / what is draft #1' from, instead of guessing."""
    from studio.campaign_state import campaign_state, describe_draft, describe_state

    state = campaign_state(run_id, dsn=dsn)
    lines = [describe_state(state)]
    if state.get("draft_1"):
        lines.append(describe_draft(state, 1))
    return " ".join(lines)


def voice_instructions_with_state(
    tenant_id: str, run_id: str | None, *, dsn: str | None = None
) -> str:
    """Voice instructions (with docs) PLUS the current run's real-state briefing injected,
    so the supervisor narrates the run from real rows. With no active run it degrades to
    the docs-aware instructions (no fabricated state). Read-only: adds NO tool — the voice
    agent stays send-incapable (exactly two tools)."""
    base = voice_instructions_with_docs(tenant_id, dsn=dsn)
    if not run_id:
        return base
    try:
        briefing = voice_state_briefing(run_id, dsn=dsn)
    except Exception:
        return base
    return (
        f"{base}\n\nSTATE — the REAL, current state of the running campaign (answer every "
        f"question about drafts / agents / counts from THIS, never from memory or a guess):"
        f"\n{briefing}"
    )


def build_session_config(
    *, instructions: str = VOICE_INSTRUCTIONS, voice: str = REALTIME_VOICE
) -> dict[str, Any]:
    """The realtime session config minted for the browser. Declares the two-tool
    surface + input transcription (so the server receives the operator's spoken
    transcript for the go-phrase factor of the GO-gate)."""
    return {
        "type": "realtime",
        "model": REALTIME_MODEL,
        "instructions": instructions,
        "tools": VOICE_TOOLS,
        "tool_choice": "auto",
        "audio": {
            "input": {"transcription": {"model": "gpt-4o-mini-transcribe"}},
            "output": {"voice": voice},
        },
    }


def mint_realtime_secret(
    api_key: str, *, session_config: dict[str, Any] | None = None, timeout: float = 30.0
) -> dict[str, Any]:
    """Call OpenAI's ``POST /v1/realtime/client_secrets`` with the SERVER-SIDE raw key
    and return the parsed JSON ({value, expires_at, session}). ``value`` is the
    short-TTL ephemeral ``ek_...`` secret the browser uses for WebRTC — the raw key
    never leaves this process. Raises ``urllib.error.HTTPError`` on a non-2xx so the
    route can surface an honest error (never a fabricated secret)."""
    body = json.dumps({"session": session_config or build_session_config()}).encode("utf-8")
    req = urllib.request.Request(
        _CLIENT_SECRETS_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed OpenAI URL)
        return json.loads(resp.read().decode("utf-8"))


# --------------------------------------------------------------------------- #
# Server-side 2-factor GO-gate (NOT model-trusted)
# --------------------------------------------------------------------------- #

# Factor 2a: explicit launch phrases. Matched on word boundaries so "go" never
# fires on "instagram"/"ago" and "do it" is a phrase, not the letters d-o.
_GO_PATTERNS = [
    re.compile(p)
    for p in (
        r"\bgo\b",
        r"\bgo ahead\b",
        r"\brun it\b",
        r"\brun the campaign\b",
        r"\blet'?s go\b",
        r"\bdo it\b",
        r"\bkick it off\b",
        r"\blaunch it\b",
        r"\blaunch the campaign\b",
    )
]

# Factor 2b: edit/instruction markers. If an utterance carries one of these it is an
# EDIT (or a further instruction), NOT a launch — even if it also contains a go-word.
# This is what makes "go ahead and add instagram" an edit, never a launch.
_EDIT_PATTERNS = [
    re.compile(p)
    for p in (
        r"\badd\b",
        r"\bchange\b",
        r"\bremove\b",
        r"\bdrop\b",
        r"\binclude\b",
        r"\bswap\b",
        r"\bswitch\b",
        r"\bset\b",
        r"\bupdate\b",
        r"\bmake it\b",
        r"\binstead\b",
        r"\balso\b",
        r"\buse\b",
        r"\bedit\b",
        r"\bwhat about\b",
        r"\bcan you\b",
        r"\bcould you\b",
    )
]


def _normalize(transcript: str | None) -> str:
    return re.sub(r"\s+", " ", (transcript or "").strip().lower())


def _has_go_phrase(text: str) -> bool:
    return any(p.search(text) for p in _GO_PATTERNS)


def _has_edit_marker(text: str) -> bool:
    return any(p.search(text) for p in _EDIT_PATTERNS)


def classify_utterance(transcript: str | None) -> str:
    """Classify a spoken utterance: 'go' (explicit launch), 'edit' (an instruction /
    plan edit), or 'other'. An utterance carrying an edit marker is ALWAYS an edit,
    even if it also contains a go-word — so 'go ahead and add instagram' is an edit."""
    text = _normalize(transcript)
    if not text:
        return "other"
    if _has_edit_marker(text):
        return "edit"
    if _has_go_phrase(text):
        return "go"
    return "other"


def plan_is_runnable(plan: CampaignPlan) -> bool:
    """Server-side arming predicate. The GO-gate may only arm (AWAITING_GO=true) once
    the plan is readback-ready: it has a goal, an audience, and at least one channel.
    A plan missing any of these can NEVER be launched, regardless of what is said."""
    return bool(
        (plan.goal or "").strip()
        and (plan.audience or "").strip()
        and [c for c in (plan.channels or []) if (c or "").strip()]
    )


def evaluate_go_gate(*, awaiting_go: bool, transcript: str | None) -> dict[str, Any]:
    """The pure 2-factor decision. Launch iff (1) AWAITING_GO is armed (plan is
    readback-ready, set server-side) AND (2) the transcript is an explicit go-phrase
    that is NOT an edit. Returns the decision + an honest reason. No I/O — unit-testable."""
    classification = classify_utterance(transcript)
    is_go = classification == "go"
    launch = bool(awaiting_go and is_go)
    if launch:
        reason = "armed + explicit go-phrase"
    elif not awaiting_go:
        reason = "not armed: plan readback not complete (goal/audience/channels required)"
    elif classification == "edit":
        reason = "utterance is an EDIT/instruction, not a launch"
    else:
        reason = "no explicit go-phrase in the utterance"
    return {
        "launch": launch,
        "armed": bool(awaiting_go),
        "classification": classification,
        "reason": reason,
    }


# --------------------------------------------------------------------------- #
# FastAPI mount
# --------------------------------------------------------------------------- #


def mount_studio_voice(app) -> None:
    """Mount the three voice seams alongside ``POST /studio/agui`` + ``/studio/run``."""
    from fastapi.responses import JSONResponse

    from obsapi.db import get_dsn

    if getattr(app.state, "_studio_voice_mounted", False):
        return
    app.state._studio_voice_mounted = True

    @app.post("/studio/voice/session")
    async def studio_voice_session(request: Request):  # noqa: ANN202
        """Mint a short-TTL ephemeral Realtime client secret. The raw OPENAI_API_KEY
        stays server-side; the browser receives only the ``ek_...`` value + the
        two-tool session config. Honest failure if the key is missing or OpenAI errors."""
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return JSONResponse(
                {"ok": False, "error": "OPENAI_API_KEY not configured (server-side)."},
                status_code=503,
            )
        # Inject the active persistent documents into the voice supervisor's
        # instructions so it truthfully knows it has the operator's docs. Best-effort
        # seed first so a fresh demo already has the brand playbook. Read-only — the
        # tool surface stays exactly two (send-incapable).
        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")
        dsn = get_dsn()
        try:
            from studio.agui import _ensure_docs_seeded

            await _to_thread(_ensure_docs_seeded, app, dsn, tenant_id)
        except Exception:
            pass
        instructions = await _to_thread(
            voice_instructions_with_docs, tenant_id, dsn=dsn
        )
        cfg = build_session_config(instructions=instructions)
        try:
            minted = await _to_thread(mint_realtime_secret, api_key, session_config=cfg)
        except urllib.error.HTTPError as exc:  # honest upstream error
            detail = exc.read().decode("utf-8", "replace")[:500]
            return JSONResponse(
                {"ok": False, "error": f"OpenAI client_secrets HTTP {exc.code}", "detail": detail},
                status_code=502,
            )
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status_code=502
            )

        # Return ONLY the ephemeral secret + non-sensitive session echo. Never the key.
        return JSONResponse(
            {
                "ok": True,
                "value": minted.get("value"),
                "expiresAt": minted.get("expires_at"),
                "model": REALTIME_MODEL,
                "tools": [t["name"] for t in VOICE_TOOLS],
                "callUrl": "https://api.openai.com/v1/realtime/calls",
            }
        )

    @app.post("/studio/voice/plan")
    async def studio_voice_plan(request: Request):  # noqa: ANN202
        """Server handler for the model's ``update_plan`` tool call. Applies the changed
        fields to THIS session's plan, persists via the same ``_persist_plan`` seam the
        run uses, and reports whether the plan is now readback-ready (which is what
        arms the GO-gate, server-side). NEVER launches anything."""
        dsn = get_dsn()
        payload = await _json_body(request)
        session_id = _session_id(payload, request)
        fields = payload.get("fields")
        if not isinstance(fields, dict):
            fields = {k: payload[k] for k in ("goal", "audience", "channels", "sections", "schedule") if k in payload}

        plan = await _to_thread(_load_plan, session_id, dsn)
        for key in ("goal", "audience", "channels", "sections", "schedule"):
            if key in fields and fields[key] is not None:
                setattr(plan, key, fields[key])
        await _to_thread(_persist_plan, dsn, session_id, plan)

        runnable = plan_is_runnable(plan)
        readback = _readback_text(plan)
        return JSONResponse(
            {
                "ok": True,
                "plan": plan.model_dump(),
                "awaitingGo": runnable,
                "runnable": runnable,
                "readback": readback,
            }
        )

    @app.post("/studio/voice/orchestrate")
    async def studio_voice_orchestrate(request: Request):  # noqa: ANN202
        """Server handler for ``request_orchestration``, guarded by the SERVER-SIDE
        2-factor GO-gate. AWAITING_GO is recomputed authoritatively from the PERSISTED
        plan (never trusted from the client/model). On a valid GO it launches the
        EXISTING held ``POST /studio/run`` spine and returns run info; otherwise it
        refuses with an honest reason. NOTHING is sent on any path."""
        dsn = get_dsn()
        payload = await _json_body(request)
        session_id = _session_id(payload, request)
        transcript = payload.get("transcript") or payload.get("utterance") or ""
        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")

        plan = await _to_thread(_load_plan, session_id, dsn)
        # Factor 1 (armed) is derived server-side from the persisted plan, not the model.
        awaiting_go = plan_is_runnable(plan)
        gate = evaluate_go_gate(awaiting_go=awaiting_go, transcript=transcript)

        if not gate["launch"]:
            return JSONResponse({"ok": True, "launched": False, "gate": gate})

        info = await launch_studio_run(
            app, dsn, session_id, tenant_id, plan,
            trigger_note=f"[voice GO] {transcript}".strip(),
        )
        return JSONResponse(
            {
                "ok": True,
                "launched": True,
                "runId": info["runId"],
                "campaignId": info["campaignId"],
                "status": info["status"],
                "gate": gate,
            }
        )


def _readback_text(plan: CampaignPlan) -> str:
    chans = ", ".join(c for c in (plan.channels or []) if c) or "no channels yet"
    return (
        f"Goal: {plan.goal or '—'}. Audience: {plan.audience or '—'}. "
        f"Channels: {chans}."
    )


async def _to_thread(fn, *args, **kwargs):
    import asyncio

    return await asyncio.to_thread(lambda: fn(*args, **kwargs))


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        return json.loads(await request.body() or b"{}")
    except Exception:
        return {}


def _session_id(payload: dict[str, Any], request: Request) -> str:
    return (
        payload.get("sessionId")
        or payload.get("threadId")
        or request.query_params.get("session_id")
        or "studio-default"
    )
