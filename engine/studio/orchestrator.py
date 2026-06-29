"""Campaign Studio orchestration (Phase-1) — real pipeline invocation.

Orchestrates the real content generation pipeline: creates a durable campaign run,
invokes run_content_to_review for each channel (which generates a real draft,
runs the real cross-family jury, and records a PENDING action), and returns the
campaign trajectory with action IDs.

The generated actions are routed under autonomy HOLD (bead-439) — drafts land as
PENDING in the review queue and require explicit human approval before publishing.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from actions import store as actions_store
from contentrun import run_content_to_review
from harness.runstore import PostgresRunStore, RunStatus
from harness.spans import Span, summarize


def _now_iso() -> str:
    """UTC ISO 8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _brief_to_text(brief: dict[str, Any]) -> str:
    """Convert a campaign brief dict to a text description for the content cell.

    Formats the goal, audience, and constraints into a cohesive campaign brief
    that the content cell can use as grounding.
    """
    goal = brief.get("goal", "").strip()
    audience = brief.get("audience", "").strip()
    constraints = brief.get("constraints") or ""
    hooks = brief.get("hooks") or []

    parts = []
    if goal:
        parts.append(goal)
    if audience:
        parts.append(f"Target audience: {audience}")
    if hooks:
        hook_items = [h for h in hooks if h]
        if hook_items:
            parts.append("Hooks to use: " + ", ".join(hook_items))
    if constraints:
        # schema types `constraints` as a String; tolerate a dict defensively
        if isinstance(constraints, dict):
            constraint_items = [f"{k}: {v}" for k, v in constraints.items() if v]
            if constraint_items:
                parts.append("Constraints: " + ", ".join(constraint_items))
        else:
            parts.append(f"Constraints: {constraints}")

    return " ".join(parts)


def start_campaign(
    tenant_id: str,
    brief: dict[str, Any],
    *,
    dsn: str | None = None,
) -> dict[str, Any]:
    """Start a real campaign orchestration.

    Creates a campaign run in the durable store, invokes the real content generation
    pipeline (contentrun.run_content_to_review) for each channel in the brief,
    records pending actions in the review queue, and returns the campaign trajectory.

    The campaign runs under autonomy HOLD (bead-439 safety hold), forcing explicit
    human approval of all generated drafts — they land as PENDING actions and cannot
    auto-fire.

    Args:
        tenant_id: The tenant account ID (e.g., studio name).
        brief: Campaign brief dict with keys:
            - goal (str): Campaign goal / theme
            - audience (str): Target audience description
            - channels (list[str]): Channels to generate for (e.g., ["instagram", "facebook"])
            - constraints (dict | None): Optional constraints (e.g., {"tone": "warm", "length": "short"})
            - hooks (dict | None): Optional campaign hooks (reserved for future use)
        dsn: Optional postgres DSN (uses ENGINE_DATABASE_URL env var if not provided).

    Returns:
        dict with keys:
            - run_id (str): The campaign run ID
            - action_ids (list[str]): Action IDs for the generated drafts (one per channel)
            - steps (list[dict]): The campaign trajectory (research -> strategy -> draft -> jury -> route)
    """

    # Initialize the durable run store
    if dsn is None:
        dsn = os.environ.get("ENGINE_DATABASE_URL") or "postgresql://scalers:scalers@localhost:5432/scalers"

    store = PostgresRunStore(dsn)
    store.setup()
    actions_store.ensure_schema(dsn)

    # Create a unique campaign run
    run_id = f"campaign-{tenant_id}-{uuid.uuid4().hex[:12]}"
    run_type = "campaign"
    trigger = "studio-ui"

    store.start_run(run_id, tenant_id, run_type, trigger)

    # Build the campaign brief text from the input dict
    brief_text = _brief_to_text(brief)

    # Record the pipeline steps (the intended trajectory)
    steps: list[Span] = []

    # Step 1: Research — STUB today (no research agent yet). HONESTY GATE: this
    # slice instruments only the REAL steps; we do NOT fabricate research content.
    # input/output/model stay null and the step is badged not-captured. Real
    # research is a separate later slice.
    step_seq = 0
    now = _now_iso()
    research_span = Span(
        span_id=uuid.uuid4().hex,
        run_id=run_id,
        node="research",
        kind="node",
        start_ts=now,
        end_ts=now,
        status="ok",
        seq=step_seq,
        text="STUB — no research agent yet; input/output not captured (P0 instruments only real steps)",
        state="research",
    )
    steps.append(research_span)
    step_seq += 1

    # Step 2: Strategy — STUB today (no strategy agent yet). Same honesty gate:
    # left null/not-captured, never fabricated.
    now = _now_iso()
    strategy_span = Span(
        span_id=uuid.uuid4().hex,
        run_id=run_id,
        node="strategy",
        kind="node",
        start_ts=now,
        end_ts=now,
        status="ok",
        seq=step_seq,
        text="STUB — no strategy agent yet; input/output not captured (P0 instruments only real steps)",
        state="strategy",
    )
    steps.append(strategy_span)
    step_seq += 1

    # Step 3: Draft (per channel via real pipeline)
    draft_start = _now_iso()
    draft_span = Span(
        span_id=uuid.uuid4().hex,
        run_id=run_id,
        node="draft",
        kind="node",
        start_ts=draft_start,
        end_ts=None,  # Will be updated after all channels
        status="ok",
        seq=step_seq,
        text="Generating drafts via content pipeline",
        state="draft",
    )

    # Invoke the REAL content generation pipeline for each channel
    channels = brief.get("channels", [])
    action_ids: list[str] = []
    # Accumulate the REAL captured per-step I/O surfaced by run_content_to_review
    # so the draft + jury node spans carry actual prompts, model outputs, and the
    # real model pins. Keyed by channel — never synthesized.
    draft_captures: list[dict[str, Any]] = []
    jury_captures: list[dict[str, Any]] = []
    channel_errors: list[dict[str, str]] = []

    for channel in channels:
        try:
            # Call the REAL synchronous content generation pipeline
            # (contentrun.py — run_content_to_review)
            result = run_content_to_review(
                tenant_id=tenant_id,
                brief=brief_text,
                channel=channel,
                action_kind="post",  # Studio generates posts
                dsn=dsn,
            )

            # The action has already been persisted by run_content_to_review via
            # actions_store.record_pending_action.
            # It's linked to the real decision with jury scores and routes to REVIEW
            # under autonomy HOLD (bead-439 safety hold).
            action_ids.append(result["action_id"])

            # Capture the REAL draft-cell I/O for the draft span (the actual prompt
            # sent to anthropic:claude-sonnet-4-6 and the typed ContentBrief it
            # returned).
            draft_captures.append(
                {
                    "channel": channel,
                    "prompt": result.get("draft_prompt"),
                    "content_brief": result.get("content_brief"),
                    "model": result.get("draft_model"),
                }
            )
            # Capture the REAL jury I/O for the jury span (the exact text scored and
            # each Opus juror's pinned model + typed JudgeScore that actually ran).
            jury_captures.append(
                {
                    "channel": channel,
                    "action": result.get("jury_action"),
                    "decision_id": result.get("decision_id"),
                    "decision": result.get("decision"),
                    "confidence": result.get("confidence"),
                    "agreement": result.get("agreement"),
                    "judges": result.get("judge_outputs") or [],
                    "judges_degraded": result.get("judges_degraded") or [],
                }
            )

        except Exception as exc:  # noqa: BLE001 — record real error, continue (degraded)
            # Continue with other channels (degraded coverage); record the real
            # error rather than silently swallowing it.
            channel_errors.append({"channel": channel, "error": f"{type(exc).__name__}: {exc}"})

    # Populate the draft span with the REAL captured cell I/O + model pin. If no
    # channel produced a draft we leave input/output/model NULL (honesty gate —
    # never fabricated) and badge the step.
    if draft_captures:
        draft_span.input, draft_span.input_truncated = summarize(
            {c["channel"]: c["prompt"] for c in draft_captures}
        )
        draft_span.output, draft_span.output_truncated = summarize(
            {c["channel"]: c["content_brief"] for c in draft_captures}
        )
        _draft_models = sorted({c["model"] for c in draft_captures if c["model"]})
        draft_span.model = (
            _draft_models[0] if len(_draft_models) == 1 else (",".join(_draft_models) or None)
        )
        draft_span.text = f"Generated {len(draft_captures)} real draft(s) via content cell"
    else:
        draft_span.status = "failed" if channel_errors else "ok"
        draft_span.text = "No draft captured (no channel produced a real draft)"
    draft_span.end_ts = _now_iso()
    steps.append(draft_span)
    step_seq += 1

    # Step 4: Jury (already run by contentrun for each draft, linked to action).
    # Populate with the REAL per-judge scores + model pins captured at the judge
    # call sites. The node model pin is the model the Anthropic Opus jurors that
    # ACTUALLY ran scored against (derived from captured per-judge models, never
    # hardcoded); the per-judge models + JudgeScores live in the span output.
    now = _now_iso()
    jury_span = Span(
        span_id=uuid.uuid4().hex,
        run_id=run_id,
        node="jury",
        kind="node",
        start_ts=now,
        end_ts=now,
        status="ok",
        seq=step_seq,
        text="Cross-family jury decision (real scores, real agreement)",
        state="jury",
    )
    if jury_captures:
        jury_span.input, jury_span.input_truncated = summarize(
            {c["channel"]: c["action"] for c in jury_captures}
        )
        jury_span.output, jury_span.output_truncated = summarize(
            {
                c["channel"]: {
                    "decision_id": c["decision_id"],
                    "decision": c["decision"],
                    "confidence": c["confidence"],
                    "agreement": c["agreement"],
                    "judges": c["judges"],  # per-seat: model pin + typed JudgeScore
                    "judges_degraded": c["judges_degraded"],
                }
                for c in jury_captures
            }
        )
        _ran = [
            (j.get("family"), j.get("model"))
            for c in jury_captures
            for j in c["judges"]
        ]
        _anthropic = sorted({m for fam, m in _ran if fam == "anthropic" and m})
        _all = sorted({m for _fam, m in _ran if m})
        _pins = _anthropic or _all
        jury_span.model = (
            _pins[0] if len(_pins) == 1 else (",".join(_pins) if _pins else None)
        )
        jury_span.text = "Cross-family jury — real per-judge scores + model pins"
    else:
        jury_span.status = "failed" if channel_errors else "ok"
        jury_span.text = "No jury captured (no channel produced a real decision)"
    steps.append(jury_span)
    step_seq += 1

    # Step 5: Route (autonomy HOLD -> REVIEW, never AUTO)
    now = _now_iso()
    route_span = Span(
        span_id=uuid.uuid4().hex,
        run_id=run_id,
        node="route",
        kind="node",
        start_ts=now,
        end_ts=now,
        status="ok",
        seq=step_seq,
        text="Routing to REVIEW (autonomy HOLD — bead-439 safety hold, approve-first)",
        state="route",
    )
    steps.append(route_span)

    # Persist all steps to the durable run store
    store.append_spans(run_id, steps)

    # Mark the campaign run complete
    store.finish_run(
        run_id,
        status=RunStatus.COMPLETED,
        auto_count=0,  # HOLD prevents auto-fire
        review_count=len(action_ids),  # All actions require human review
    )

    # Return the campaign trajectory with action IDs
    return {
        "run_id": run_id,
        "action_ids": action_ids,
        "steps": [
            {
                "node": s.node,
                "text": s.text,
                "status": s.status,
                "seq": s.seq,
            }
            for s in steps
        ],
    }
