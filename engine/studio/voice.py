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
            "interview. Call this whenever the operator states or changes ANY plan "
            "field: the goal, audience, channels, the offer, the artist, HOW MANY "
            "drafts/leads ('exactly three drafts' → lead_count=3 AND output_count=3), "
            "deep research on/off, specific leads by email, or the lead source. Pass "
            "ONLY the fields that changed. Counts are CRITICAL: if the operator says a "
            "number of drafts and you do not record it, the run will size itself and "
            "produce the wrong count. This NEVER launches anything — it only edits "
            "the plan."
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
                "offer": {
                    "type": "string",
                    "description": "The offer / call-to-action (e.g. '$1200 full-day session, payment plans').",
                },
                "artist": {"type": "string", "description": "Which artist the campaign fronts."},
                "tone": {"type": "string"},
                "campaign_type": {
                    "type": "string",
                    "description": "e.g. outreach / winback / artistspotlight / holiday.",
                },
                "lead_count": {
                    "type": "integer",
                    "description": "EXACT number of leads to target when the operator states one.",
                },
                "output_count": {
                    "type": "integer",
                    "description": "EXACT number of drafts to produce when the operator states one.",
                },
                "deep_research": {
                    "type": "boolean",
                    "description": "True when the operator asks for deep research on each lead.",
                },
                "research_depth": {
                    "type": "string",
                    "description": "light / standard / deep.",
                },
                "per_lead": {
                    "type": "boolean",
                    "description": "One personalized message per lead (true) vs one shared message.",
                },
                "lead_source": {
                    "type": "string",
                    "description": "'provided' = use ONLY the operator's own leads (uploaded/DB/named).",
                },
                "leads": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific leads by email (or exact name) when the operator names them.",
                },
                "use_conversation_history": {
                    "type": "boolean",
                    "description": "Read each lead's imported conversation thread for the psych analysis.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_run_status",
        "description": (
            "READ the REAL current state of the campaign run and the review queue "
            "from the database: run status, which agents actually ran (in order, "
            "with each lead's REAL name), and the staged drafts (position, lead "
            "name, recipient, subject) plus honest counts. Call this EVERY time the "
            "operator asks what is happening, how many drafts exist, who a draft is "
            "for, or to review drafts — and answer ONLY from its output. If it shows "
            "nothing yet, say the team is still working. NEVER answer such questions "
            "from memory: names or counts not in this tool's output are fabrication. "
            "Read-only — it cannot launch or send anything."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
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
# update_plan + get_run_status are edit/read; request_orchestration launches a HELD
# run behind the server GO-gate. There is still NO send/publish tool.
VOICE_TOOL_NAMES: tuple[str, ...] = tuple(t["name"] for t in VOICE_TOOLS)

# One GO = one run. Repeat GO utterances (and duplicate deliveries of one tool
# call) within this window return the ALREADY-launched run instead of stacking
# fresh runs — a real session stacked ~25 runs / 105 drafts this way.
_VOICE_LAUNCH_DEBOUNCE_S = 45.0
#: session_id -> (monotonic_ts, run_id, campaign_id) of the last launch.
_VOICE_LAUNCHES: dict[str, tuple[float, str, str]] = {}


def voice_run_status_snapshot(
    tenant_id: str, session_id: str, *, dsn: str | None = None
) -> dict[str, Any]:
    """The REAL run + queue state the voice host may narrate — live DB reads only.

    Composes the existing live-state seams: the most recent run id, its finalized
    leads / staged drafts (real names + recipients, in order), the current agent
    activity, and the tenant's review-queue truth block. Everything the voice model
    says about a run MUST come from here."""
    from studio.inventory import live_operations_block
    from studio.live_state import agent_activity, finalized_leads, resolve_recent_run_id

    out: dict[str, Any] = {"sessionId": session_id}
    run_id = resolve_recent_run_id(tenant_id, dsn)
    out["runId"] = run_id
    if run_id:
        try:
            from studio.live_state import get_runs_registry

            reg = get_runs_registry().get(run_id) or {}
            out["runStatus"] = str(reg.get("status") or "unknown")
        except Exception:
            out["runStatus"] = "unknown"
        if out["runStatus"] not in ("completed", "failed", "error", "not_built"):
            # A real operator was told "run completed, no drafts staged" while the
            # run was still writing its drafts — the snapshot must carry the truth
            # that counts can still GROW so the narrator never reads a mid-write
            # zero as a final zero.
            out["stagingNote"] = (
                "The run is still executing — drafts may still be staging. Say the "
                "team is still working; NEVER state a final draft count until "
                "runStatus is 'completed'."
            )
        try:
            out["drafts"] = finalized_leads(tenant_id, run_id, dsn=dsn)
        except Exception as exc:
            out["drafts"] = {"error": f"{type(exc).__name__}: {exc}"}
        try:
            out["activity"] = agent_activity(tenant_id, dsn=dsn)
        except Exception as exc:
            out["activity"] = {"error": f"{type(exc).__name__}: {exc}"}
    else:
        out["drafts"] = None
        out["activity"] = None
        out["note_no_run"] = "No campaign run exists yet for this studio."
    try:
        out["queue"] = live_operations_block(tenant_id, dsn=dsn)
    except Exception as exc:
        out["queue"] = f"unreadable: {type(exc).__name__}: {exc}"
    out["rule"] = (
        "These are the ONLY run facts, lead names, draft counts and statuses you "
        "may state. Draft #1 is the first entry in drafts. If a name or number is "
        "not here, you do not know it — say so instead of guessing."
    )
    return out

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
    "PER-LEAD MODE IS A FIELD, NOT A VIBE: when the operator wants THEIR OWN "
    "people — 'pick them from the imported conversations', 'use their real "
    "conversations', 'these three customers', names/emails, 'one message per "
    "person' — you MUST set lead_source='provided', per_lead=true and "
    "use_conversation_history=true on update_plan (plus leads=[...] when they "
    "name people, and lead_count/output_count for a stated number). Capturing "
    "it only in the audience TEXT routes the run to a generic template blast "
    "with no real recipients — a real operator hit exactly that.\n"
    "PLAN READBACK: once you have at least goal, audience, and channels, read the "
    "whole plan back out loud in one or two sentences, then ask: 'Should I run "
    "this?'\n"
    "GO-GATE: only if the operator answers with an explicit launch word (go / run "
    "it / let's go / do it / kick it off) do you call request_orchestration. If the "
    "server refuses or the tool returns an error, report that refusal/error VERBATIM "
    "and keep interviewing — saying 'we're in motion' or 'the run is launched' after "
    "a refused/failed launch is fabrication. A launch is real ONLY when the tool "
    "result includes a run id; say that run id out loud.\n"
    "ORCHESTRATE: after a successful launch, narrate ONLY agent steps that actually "
    "appear in the run-state briefing, with their REAL outputs. If the state shows no "
    "agents landed yet, say exactly that — 'the run is queued, no agent has landed "
    "yet' — and wait for real steps; NEVER describe what the strategist / researcher "
    "/ copywriter 'has outlined', 'has delivered', or 'is doing' unless that step and "
    "its output are reported in the state. Pre-narrating the expected pipeline as if "
    "it already happened is fabrication. Make clear everything is HELD for approval "
    "and nothing was sent.\n\n"
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
    "/ jury ran, what draft #1 (or #N) says and WHO it is to, or why it was written, or "
    "asks to REVIEW the drafts: CALL the get_run_status tool FIRST and answer ONLY from "
    "its output. NEVER invent a lead name, a count, or a status — a name that did not "
    "come from get_run_status is fabrication. Draft #1 is the FIRST entry in the tool's "
    "drafts list — say that exact name so it matches what the operator sees on screen. "
    "While a run executes, do NOT narrate steps you have not read: call get_run_status "
    "again for fresh steps, and if nothing new landed, say the team is still working. "
    "If a required step (e.g. the strategist or critic) is reported as failed, say so "
    "honestly — do not claim the run finished cleanly. If the tool errors, say you "
    "cannot read the state right now rather than guessing.\n\n"
    "ONE GO = ONE RUN — after a successful launch, NEVER call request_orchestration "
    "again for the same campaign. A repeated 'go ahead' from the operator while a run "
    "is executing is them being polite, not a new launch order; the server will also "
    "return the SAME run id if you do. Only a NEW plan (new interview) may launch a "
    "new run.\n\n"
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
        from studio.inventory import build_data_inventory, live_operations_block

        readback = build_data_inventory(tenant_id, dsn=dsn)
    except Exception:
        return ""
    if not readback:
        return ""
    # LIVE OPERATIONS STATE from the same shared builder as the chat host: the
    # true queue/run numbers are IN the instructions, so the voice supervisor
    # never has a reason (or license) to estimate them.
    try:
        ops = live_operations_block(tenant_id, dsn=dsn)
    except Exception:
        ops = ""
    block = "\n\n" + readback
    if ops:
        block += "\n\n" + ops
    return block + "\n\n" + campaign_interview_prompt()


def live_state_snapshot(tenant_id: str, *, dsn: str | None = None) -> dict[str, Any]:
    """The LIVE studio state bundle (engine-core item 4): active run + per-agent
    latest steps + pending artwork selection + staged leads + file registry — read
    FRESH from the DB at call time (never the mint-time frozen context). This is the
    ``liveState`` key on the session-mint, plan, and orchestrate responses: the
    browser relay feeds it back to the model as tool output, so the voice
    supervisor answers state questions from CURRENT rows every turn. Best-effort:
    an unreadable store yields an honest error entry, never a fabricated state."""
    try:
        from studio.live_state import snapshot

        return snapshot(tenant_id, dsn=dsn)
    except Exception as exc:
        return {
            "error": f"live state unavailable: {type(exc).__name__}",
            "note": "answer state questions honestly as unknown — do not guess",
        }


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


def conversation_briefing(
    session_id: str, *, dsn: str | None = None, max_turns: int = 24, max_chars: int = 320
) -> str:
    """The session's REAL prior conversation (typed AND spoken turns, from
    ``studio_chat_turns``) rendered as an instruction block, so a freshly minted
    voice session CONTINUES the same conversation instead of re-greeting. Honest
    empty string when the session has no history or the store is unreachable."""
    try:
        from studio.agui import _chat_store

        turns = _chat_store(dsn).history(session_id)
    except Exception:
        return ""
    if not turns:
        return ""
    lines = []
    for t in turns[-max_turns:]:
        role = (t.role or "").upper()
        text = (t.text or "").strip().replace("\n", " ")
        if not text:
            continue
        lines.append(f"- {role}: {text[:max_chars]}")
    if not lines:
        return ""
    return (
        "\n\nCONVERSATION SO FAR — this session's REAL prior turns (typed and spoken), "
        "oldest to newest. You already know all of this: CONTINUE the conversation, do "
        "NOT re-greet, and do NOT re-ask for facts already given below.\n"
        + "\n".join(lines)
    )


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
        payload = await _json_body(request)
        session_id = _session_id(payload, request)
        try:
            from studio.agui import _ensure_docs_seeded

            await _to_thread(_ensure_docs_seeded, app, dsn, tenant_id)
        except Exception:
            pass
        instructions = await _to_thread(
            voice_instructions_with_docs, tenant_id, dsn=dsn
        )
        # ONE conversation across voice and text: the mint carries this session's
        # real prior turns so the spoken host CONTINUES the thread, never re-greets.
        instructions += await _to_thread(conversation_briefing, session_id, dsn=dsn)
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
        # ``liveState`` (documented key, item 4) is the CURRENT studio state at mint —
        # and the SAME snapshot is re-read fresh on every /studio/voice/plan and
        # /studio/voice/orchestrate response, so the session's view never freezes.
        live = await _to_thread(live_state_snapshot, tenant_id, dsn=dsn)
        return JSONResponse(
            {
                "ok": True,
                "value": minted.get("value"),
                "expiresAt": minted.get("expires_at"),
                "model": REALTIME_MODEL,
                "tools": [t["name"] for t in VOICE_TOOLS],
                "callUrl": "https://api.openai.com/v1/realtime/calls",
                "liveState": live,
            }
        )

    @app.post("/studio/voice/turn")
    async def studio_voice_turn(request: Request):  # noqa: ANN202
        """Persist ONE finalized spoken line into the session's shared transcript
        (``studio_chat_turns``) — the same store the typed host reads — so voice and
        text are ONE conversation: a later typed turn (or a re-minted voice session)
        knows what was said aloud. Roles are restricted to operator/host; nothing
        here can launch or send anything."""
        payload = await _json_body(request)
        session_id = _session_id(payload, request)
        role = str(payload.get("role") or "").strip().lower()
        text = str(payload.get("text") or "").strip()
        if role not in ("operator", "host"):
            return JSONResponse(
                {"ok": False, "error": "role must be 'operator' or 'host'"}, status_code=400
            )
        if not text:
            return JSONResponse({"ok": False, "error": "text is empty"}, status_code=400)
        try:
            from studio.agui import _log_turn

            await _to_thread(_log_turn, get_dsn(), session_id, role, text[:4000], "voice-realtime")
        except Exception as exc:  # honest failure; the display copy already exists client-side
            return JSONResponse(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status_code=500
            )
        return JSONResponse({"ok": True})

    @app.post("/studio/voice/plan")
    async def studio_voice_plan(request: Request):  # noqa: ANN202
        """Server handler for the model's ``update_plan`` tool call. Applies the changed
        fields to THIS session's plan, persists via the same ``_persist_plan`` seam the
        run uses, and reports whether the plan is now readback-ready (which is what
        arms the GO-gate, server-side). NEVER launches anything."""
        dsn = get_dsn()
        payload = await _json_body(request)
        session_id = _session_id(payload, request)
        _PLAN_FIELDS = (
            "goal", "audience", "channels", "sections", "schedule",
            "offer", "artist", "tone", "campaign_type",
            "lead_count", "output_count", "deep_research", "research_depth",
            "per_lead", "lead_source", "leads", "use_conversation_history",
        )
        fields = payload.get("fields")
        if not isinstance(fields, dict):
            fields = {k: payload[k] for k in _PLAN_FIELDS if k in payload}

        plan = await _to_thread(_load_plan, session_id, dsn)
        # Apply EVERY run-shaping field the interview can gather — the handler
        # previously accepted only goal/audience/channels/sections/schedule, so a
        # spoken "exactly three drafts, deep research on" was silently DROPPED and
        # the run sized itself off stale plan state (a real operator watched a
        # '3 drafts' ask fan out to a 31-lead cohort because of this).
        for key in _PLAN_FIELDS:
            if key in fields and fields[key] is not None:
                value = fields[key]
                if key in ("lead_count", "output_count"):
                    try:
                        value = max(0, int(value))
                    except (TypeError, ValueError):
                        continue
                elif key in ("deep_research", "per_lead", "use_conversation_history"):
                    value = bool(value)
                elif key == "leads":
                    if not isinstance(value, list):
                        continue
                    value = [str(h).strip() for h in value if str(h or "").strip()]
                setattr(plan, key, value)
        await _to_thread(_persist_plan, dsn, session_id, plan)

        runnable = plan_is_runnable(plan)
        readback = _readback_text(plan)
        # LIVE re-read (item 4): the response carries the CURRENT studio state, so
        # the voice model answers from fresh rows instead of the mint-time context.
        # Additive key — the existing contract (ok/plan/awaitingGo/runnable/readback)
        # is unchanged.
        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")
        live = await _to_thread(live_state_snapshot, tenant_id, dsn=dsn)
        return JSONResponse(
            {
                "ok": True,
                "plan": plan.model_dump(),
                "awaitingGo": runnable,
                "runnable": runnable,
                "readback": readback,
                "liveState": live,
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

        # LIVE re-read (item 4, additive): fresh state rides on BOTH branches so the
        # voice supervisor can narrate the real run/selection state each turn.
        live = await _to_thread(live_state_snapshot, tenant_id, dsn=dsn)
        if not gate["launch"]:
            return JSONResponse(
                {"ok": True, "launched": False, "gate": gate, "liveState": live}
            )

        # LAUNCH DEBOUNCE (per session): a real operator's session showed every
        # repeated "go ahead" — and duplicate tool-call deliveries of ONE go —
        # launching a FRESH run each time; ~25 stacked runs filled the review queue
        # with 105 drafts. One GO = one run: while a run this session launched in
        # the last window is still fresh, a repeat GO returns THAT run id instead
        # of launching another.
        import time as _time

        now = _time.monotonic()
        last = _VOICE_LAUNCHES.get(session_id)
        if last is not None and (now - last[0]) < _VOICE_LAUNCH_DEBOUNCE_S:
            return JSONResponse(
                {
                    "ok": True,
                    "launched": True,
                    "runId": last[1],
                    "campaignId": last[2],
                    "status": "already-running",
                    "deduped": True,
                    "gate": gate,
                    "liveState": live,
                    "note": (
                        f"Run {last[1]} was already launched by this session "
                        f"{int(now - last[0])}s ago — NOT launching a duplicate. "
                        "Narrate that run; do not claim a second run started."
                    ),
                }
            )

        info = await launch_studio_run(
            app, dsn, session_id, tenant_id, plan,
            trigger_note=f"[voice GO] {transcript}".strip(),
        )
        _VOICE_LAUNCHES[session_id] = (now, info["runId"], info["campaignId"])
        return JSONResponse(
            {
                "ok": True,
                "launched": True,
                "runId": info["runId"],
                "campaignId": info["campaignId"],
                "status": info["status"],
                "gate": gate,
                "liveState": live,
            }
        )

    @app.post("/studio/voice/run_status")
    async def studio_voice_run_status(request: Request):  # noqa: ANN202
        """Server handler for the model's read-only ``get_run_status`` tool: the REAL
        run + review-queue state from the database, so the voice host answers
        'what's happening / who is draft #1 for / how many drafts' from rows —
        never from imagination (a real operator was told invented lead names).
        Read-only: cannot launch or send anything."""
        dsn = get_dsn()
        payload = await _json_body(request)
        session_id = _session_id(payload, request)
        tenant_id = os.environ.get("STUDIO_TENANT_ID", "demo")
        try:
            snapshot = await _to_thread(
                voice_run_status_snapshot, tenant_id, session_id, dsn=dsn
            )
        except Exception as exc:
            return JSONResponse(
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "note": "Could not read the run state — say so honestly; do not guess.",
                }
            )
        return JSONResponse({"ok": True, **snapshot})


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
