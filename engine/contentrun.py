"""Served content route (a9m.7) — a REAL content run that produces a REAL decision.

The demo's review queue was seeded with placeholder jury scores
(:mod:`actions.seed_demo`). This module is the live counterpart: one call generates
a real brand-copy draft, scores it with the **real cross-family jury** + the
**computed** confidence (self-consistency pooled with jury quality — never the old
``0.9`` constant), routes it under the bead-439 autonomy **HOLD**, and persists a
PENDING :class:`actions` row linked to the real ``autonomy_decisions`` /
``autonomy_jury`` rows. The console jury card then renders real scores.

Pipeline (all real, no replay):

1. **Generate** — a real :class:`~cells.content_brief.ContentBrief` from ``brief``
   via the typed content cell (temp-0, Anthropic key from the process env). The
   caption + CTA + hashtags become the post draft.
2. **Probe** — re-sample the same cell with a temp>0 probe ``K`` times and score
   self-consistency (generator stability). Too few samples → ``None`` → the
   decision fails safe to review (never a confident default).
3. **Score + route** — :func:`autonomy.produce.produce_and_record_decision_real`
   runs the real cross-family panel (≥2 Anthropic Opus jurors + a local Ollama
   juror), aggregates per dimension with the hard-fail floor, **computes** the
   calibrated confidence from jury quality + self-consistency, and derives the
   route under ``AutonomyMode.HOLD``. A judge that times out / errors / is
   unavailable is **dropped** (degraded coverage), which routes REVIEW — it can
   never be counted as agreement and can never enable AUTO.
4. **Persist** — :func:`actions.store.record_pending_action` writes the PENDING
   action, linking the real ``decision_id`` and carrying conf / threshold /
   esc{kind,label} straight off the decision, idempotent on the content key.

Safety invariants kept intact: autonomy stays HOLD (439 is never lifted here), the
two-layer HOLD short-circuits to REVIEW before the confidence check, and a degraded
or missing judge routes REVIEW — never AUTO, never a fabricated score.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid

from actions import store as actions_store
from autonomy.judges import DEFAULT_PANEL, JudgeScore, JudgeSpec, build_judge_cell
from autonomy.confidence import DEFAULT_K, probe_self_consistency
from autonomy.produce import produce_and_record_decision_real
from autonomy.store import PostgresDecisionStore
from cells.content_brief import ContentBrief, Platform, build_content_brief_cell
from harness.router import DEFAULT_THRESHOLD
from harness.state import AutonomyMode
from sideeffects.keys import idempotency_key

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

# Probe temperature for the self-consistency sampler. Separate from the temp-0
# decision draft (confidence.py): the probe MEASURES generator stability, so it
# must vary; the persisted draft is the deterministic temp-0 one.
_PROBE_TEMPERATURE = 0.8

# Channel -> the content Platform the typed cell drafts for. Email/outreach uses a
# different cell (copywriter email) and is out of scope for this social route — an
# unmapped channel raises rather than silently producing the wrong artifact.
_PLATFORM_FOR_CHANNEL: dict[str, Platform] = {
    "instagram": Platform.INSTAGRAM,
    "facebook": Platform.FACEBOOK,
}

# action_kind -> the console "worker" label (who proposed it). Cosmetic; the
# decision/route are unaffected.
_WORKER_FOR_KIND: dict[str, str] = {
    "post": "Publisher",
    "outreach": "Outreach",
    "comment": "Responder",
    "reply": "Responder",
}


def _dsn() -> str:
    return os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def _normalize(text: str) -> str:
    """Whitespace/case-normalized reduction used as the self-consistency signature."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _render_post(brief: ContentBrief) -> str:
    """Render the typed brief into the post text the operator reviews + the jury scores."""
    parts = [brief.caption.strip()]
    cta = (brief.call_to_action or "").strip()
    if cta:
        parts.append(cta)
    if brief.hashtags:
        parts.append(" ".join(f"#{h.lstrip('#')}" for h in brief.hashtags))
    return "\n\n".join(p for p in parts if p)


def _build_prompt(
    tenant_id: str, brief: str, platform: Platform, strategy: str | None = None
) -> str:
    """Campaign context for the content cell (grounding before task).

    When a real campaign strategy was produced upstream (slice-2 strategy agent),
    it is composed into the prompt between the brief and the task so the draft is
    grounded by the strategy — the angle/positioning/messages the draft must carry,
    not decoration.
    """
    parts = [
        f"Studio/account: @{tenant_id} — a women-led tattoo studio with a warm, "
        f"concrete, human voice.",
        f"Platform: {platform.value}",
        f"Campaign brief: {brief}",
    ]
    if strategy and strategy.strip():
        parts.append(
            "Campaign strategy (produced upstream by the strategy agent — lead with "
            "this angle and land these messages):\n" + strategy.strip()
        )
    parts.append(
        "Produce ONE organic social post brief in the studio's brand voice. Write "
        "like a real person, not a brand — no AI boilerplate, no placeholders."
    )
    return "\n".join(parts)


def run_content_to_review(
    tenant_id: str,
    brief: str,
    channel: str,
    action_kind: str,
    *,
    target: str | None = None,
    strategy: str | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    probe_k: int = DEFAULT_K,
    panel: tuple[JudgeSpec, ...] = DEFAULT_PANEL,
    dsn: str | None = None,
) -> dict:
    """Synchronous entry point: run one real content action end to end and return a
    JSON-serializable summary (decision id, real jury scores, route, action id,
    judges ran vs degraded). Drives the async pipeline via :func:`asyncio.run`, so
    it must NOT be called from inside a running event loop — an in-loop caller (e.g.
    the FastAPI obs-API) awaits :func:`run_content_to_review_async` instead.

    ``strategy`` is the rendered upstream campaign strategy (slice-2 strategy agent),
    folded into the draft prompt so the draft is grounded by the real plan."""
    return asyncio.run(
        run_content_to_review_async(
            tenant_id,
            brief,
            channel,
            action_kind,
            target=target,
            strategy=strategy,
            threshold=threshold,
            probe_k=probe_k,
            panel=panel,
            dsn=dsn,
        )
    )


async def run_content_to_review_async(
    tenant_id: str,
    brief: str,
    channel: str,
    action_kind: str,
    *,
    target: str | None = None,
    strategy: str | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    probe_k: int = DEFAULT_K,
    panel: tuple[JudgeSpec, ...] = DEFAULT_PANEL,
    dsn: str | None = None,
) -> dict:
    """Run one real content action end to end and persist a PENDING review decision.

    Generates a real draft from ``brief``, scores + routes it with the real
    cross-family jury and the computed confidence under autonomy HOLD, and writes a
    PENDING action linked to the real decision. Returns a JSON-serializable summary
    including the decision id, the real per-judge jury scores, the route (always
    ``review`` — HOLD + degraded coverage never AUTO), the action id, and which
    judge families actually ran vs degraded.
    """
    platform = _PLATFORM_FOR_CHANNEL.get(channel)
    if platform is None:
        raise ValueError(
            f"channel {channel!r} is not a social content channel "
            f"(supported: {sorted(_PLATFORM_FOR_CHANNEL)}); email/outreach copy uses "
            "the copywriter-email cell, which is out of scope for this route."
        )

    dsn = dsn or _dsn()
    decision_store = PostgresDecisionStore(dsn)
    decision_store.setup()
    actions_store.ensure_schema(dsn)

    run_id = f"contentrun-{tenant_id}-{uuid.uuid4().hex[:12]}"
    decision_id = f"{run_id}-decision"
    prompt = _build_prompt(tenant_id, brief, platform, strategy)

    # 1. REAL draft (temp-0, the decision artifact).
    content_cell = build_content_brief_cell()
    # The pinned model id the draft cell actually runs against (default
    # "anthropic:claude-haiku-4-5"). Read off the cell, never hardcoded — if the
    # cell is routed to another model the captured pin follows it.
    draft_model = str(content_cell.model)
    drafted: ContentBrief = await content_cell.run(prompt)
    draft = _render_post(drafted)

    # 2. REAL self-consistency probe (temp>0; same cell, separate from the draft).
    probe_cell = build_content_brief_cell(temperature=_PROBE_TEMPERATURE)

    async def _sample() -> ContentBrief:
        return await probe_cell.run(prompt)

    self_consistency = await probe_self_consistency(
        _sample, k=probe_k, signature=lambda b: _normalize(b.caption)
    )

    # 3. REAL cross-family jury + COMPUTED confidence, routed under HOLD. The
    # tracking runner records the real reason each seat ran or dropped (e.g. an
    # unreachable local Ollama juror) without re-running the panel.
    ran: dict[str, str] = {}
    dropped: dict[str, str] = {}
    # Capture each REAL judge call's pinned model + typed JudgeScore at the call
    # site, keyed by seat. Only seats that actually returned a score land here —
    # a dropped/unavailable seat (e.g. local Ollama) is absent, never fabricated.
    judge_captures: dict[str, dict] = {}

    async def _runner(spec: JudgeSpec, action: str) -> JudgeScore:
        try:
            score = await build_judge_cell(spec).run(action)
        except Exception as exc:  # noqa: BLE001 — record the real drop reason, re-raise
            dropped[spec.name] = f"{type(exc).__name__}: {exc}"
            raise
        ran[spec.name] = spec.family
        judge_captures[spec.name] = {
            "judge": spec.name,
            "family": spec.family,
            "model": spec.model,
            "framing": spec.framing,
            "score": score.model_dump(mode="json"),
        }
        return score

    record = await produce_and_record_decision_real(
        decision_store,
        decision_id=decision_id,
        run_id=run_id,
        tenant_id=tenant_id,
        channel=channel,
        action_kind=action_kind,
        action=draft,
        threshold=threshold,
        autonomy=AutonomyMode.HOLD,  # 439 never lifted; HOLD short-circuits to REVIEW
        panel=panel,
        judge_runner=_runner,
        self_consistency=self_consistency,
    )

    # 4. Persist the PENDING action linked to the REAL decision (idempotent on key).
    idem = idempotency_key(tenant_id, channel, target or "", draft)
    action_id = actions_store.record_pending_action(
        tenant_id=tenant_id,
        decision_id=record.decision_id,
        type=action_kind,
        channel=channel,
        worker=_WORKER_FOR_KIND.get(action_kind, "Publisher"),
        target=target,
        draft=draft,
        conf=record.pooled_confidence,
        threshold=record.threshold,
        esc_kind=record.esc.kind.value,
        esc_label=record.esc.label,
        idempotency_key=idem,
        run_id=run_id,
        dsn=dsn,
    )

    # Authoritative ran/degraded from the persisted votes vs the configured panel;
    # the tracking dicts enrich the drop reasons.
    ran_names = {v.judge for v in record.jury}
    judges_ran = [{"name": v.judge, "family": v.family} for v in record.jury]
    judges_degraded = [
        {
            "name": s.name,
            "family": s.family,
            "model": s.model,
            "reason": dropped.get(s.name, "unavailable (timeout/cancelled)"),
        }
        for s in panel
        if s.name not in ran_names
    ]
    families_reachable = sorted({v.family for v in record.jury if v.family})

    return {
        "tenant_id": tenant_id,
        "channel": channel,
        "action_kind": action_kind,
        "target": target,
        "run_id": run_id,
        "decision_id": record.decision_id,
        "action_id": action_id,
        "decision": record.decision.value,
        "esc_kind": record.esc.kind.value,
        "esc_label": record.esc.label,
        "confidence": record.pooled_confidence,
        "threshold": record.threshold,
        "agreement": record.agreement,
        "self_consistency": record.self_consistency,
        "safety_verdict": record.safety_verdict.value,
        "draft": draft,
        "jury": [
            {
                "judge": v.judge,
                "family": v.family,
                "voice": v.voice,
                "safety": v.safety,
                "appr": v.appr,
                "on_voice": v.on_voice,
                "voice_hard_fail": v.voice_hard_fail,
                "safety_hard_fail": v.safety_hard_fail,
                "appr_hard_fail": v.appr_hard_fail,
            }
            for v in record.jury
        ],
        "jury_panel_expected": len(panel),
        "judges_ran": judges_ran,
        "judges_degraded": judges_degraded,
        "families_reachable": families_reachable,
        "jury_fully_ran": len(record.jury) == len(panel),
        "degraded": len(record.jury) < len(panel),
        # --- Captured REAL per-step I/O for span instrumentation (P0 make-real) ---
        # The actual prompt/output of the model calls this run made (not a summary),
        # so the Studio orchestrator can populate runs.steps[].input/.output/.model
        # for the draft + jury steps with real content + the real model pins.
        "draft_prompt": prompt,            # exact prompt sent to the content cell
        "draft_model": draft_model,        # e.g. "anthropic:claude-haiku-4-5"
        "content_brief": drafted.model_dump(mode="json"),  # the typed ContentBrief
        "jury_action": draft,              # exact text scored by every juror
        "judge_outputs": list(judge_captures.values()),    # per-seat model + JudgeScore
    }
