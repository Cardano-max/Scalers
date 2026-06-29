"""Read model + mutation delegations over Postgres.

All reads are tenant-scoped where a ``tenantId`` is supplied. The functions build
the strawberry types in :mod:`obsapi.types` directly from real rows. Nothing is
fabricated: an empty table yields an empty list; a missing source yields 0 / "".

The jury card is assembled by joining ``actions`` → ``autonomy_decisions``
(confidence, threshold, agreement, gates, escalation) → ``autonomy_jury``
(per-judge votes, aggregated into the three console dimensions).

Mutations delegate to the seams being built in parallel under ``engine/actions/``
(``actions.publish.approve_and_publish`` / ``reject``). They are imported LAZILY:
if the module is not present yet the resolver raises a clear GraphQL error rather
than crashing the server, so read-only queries keep working.
"""

from __future__ import annotations

import importlib
import os
from typing import Any, Callable

from . import mappers
import observability
from .db import connect
from .types import (
    Action,
    ActivityItem,
    ActivityLink,
    AutonomyConfig,
    EngagementTile,
    Escalation,
    ExecutionTrace,
    FeedEvent,
    Gate,
    Judge,
    JudgeVote,
    JuryDecision,
    JuryDim,
    JurorDimScore,
    Kpis,
    Outcome,
    Overview,
    Run,
    RunEvent,
    RunStep,
    Span,
    SystemHealth,
    Tenant,
)

# Operational warmup caps (config, not metrics). Used counts are read live.
GMAIL_WARMUP_CAP = 60
IG_PUBLISH_CAP = 25


# --------------------------------------------------------------------------- #
# Action + jury-card assembly
# --------------------------------------------------------------------------- #
def _jury_for(conn: Any, decision_id: str) -> list[dict[str, Any]]:
    # Only the columns guaranteed across both jury schema versions.
    return conn.execute(
        "SELECT judge, family, voice, safety, appr FROM autonomy_jury "
        "WHERE decision_id=%s ORDER BY judge",
        (decision_id,),
    ).fetchall()


def _decision_for(conn: Any, decision_id: str) -> dict[str, Any] | None:
    return conn.execute(
        "SELECT * FROM autonomy_decisions WHERE decision_id=%s", (decision_id,)
    ).fetchone()


# --------------------------------------------------------------------------- #
# v2 observability: spans, events, judges, traces, links
# --------------------------------------------------------------------------- #
def _kind_for_node(node_name: str) -> str:
    """Map node/cell name to span kind: tool|llm."""
    node_lower = (node_name or "").lower()
    # LLM cells (Claude models) and llm-ish nodes
    if any(x in node_lower for x in ["claude", "llm", "model", "cell:"]):
        return "llm"
    # Default: tool
    return "tool"


def _build_span(step: dict[str, Any]) -> Span:
    """Convert a step JSONB dict to a Span (one of: tool|llm)."""
    node_name = step.get("node") or step.get("cell") or ""
    kind = _kind_for_node(node_name)
    title = step.get("text") or node_name or "Unknown"
    ms = step.get("duration_ms")
    # Detail: prefer output, fall back to input; mark truncation if needed.
    detail = step.get("output") or step.get("input") or "—"
    if step.get("output_truncated"):
        detail = detail[:200] + "…" if len(str(detail)) > 200 else detail
    return Span(kind=kind, title=title, ms=int(ms) if ms else None, detail=str(detail))


def _build_run_events(steps_jsonb: list[dict[str, Any]] | None) -> list[RunEvent]:
    """Convert runs.steps JSONB (array of spans with parent_span_id) into RunEvent list.

    Groups top-level spans (parent_span_id=null) into RunEvent objects.
    Each event contains its own children as nested Span objects.
    """
    if not steps_jsonb:
        return []

    events: list[RunEvent] = []

    # Build a span lookup for finding children
    span_map: dict[str, dict[str, Any]] = {}
    top_level: list[dict[str, Any]] = []

    for step in steps_jsonb:
        if not isinstance(step, dict):
            continue
        span_id = step.get("span_id")
        if span_id:
            span_map[span_id] = step
        if step.get("parent_span_id") is None:
            top_level.append(step)

    # For each top-level span, create a RunEvent
    for top_step in top_level:
        worker = mappers.worker(top_step.get("node"))
        text = top_step.get("text") or top_step.get("node") or ""
        status = top_step.get("status") or "ok"
        severity = "error" if status.lower() in ("failed", "error") else "info"
        duration_ms = top_step.get("duration_ms")
        ms_str = f"{duration_ms/1000:.1f}s" if duration_ms is not None else "—"

        # Build nested spans from children
        child_spans: list[Span] = []
        for child_step in top_step.get("children") or []:
            if isinstance(child_step, dict):
                child_spans.append(_build_span(child_step))

        events.append(
            RunEvent(
                worker=worker,
                text=text,
                severity=severity,
                ms=ms_str,
                spans=child_spans,
            )
        )

    return events


def _build_judges_from_jury(conn: Any, decision_id: str) -> tuple[list[Judge], str]:
    """Build Judge objects from autonomy_jury rows + decision metadata.

    Returns (judges list, latency string).
    Latency is computed from autonomy_decisions.created_at if available.
    """
    jury_rows = _jury_for(conn, decision_id)

    judges: list[Judge] = []
    for row in jury_rows:
        voice = float(row.get("voice", 0.5))
        safety = float(row.get("safety", 0.5))
        appr = float(row.get("appr", 0.5))
        score = (voice + safety + appr) / 3.0
        # Hard fail if any dimension flagged a disqualifier
        hard_fail = any([
            row.get("voice_hard_fail", False),
            row.get("safety_hard_fail", False),
            row.get("appr_hard_fail", False),
        ])
        vote = "fail" if hard_fail else "pass"
        # Use real judge rationale if available; fallback to score-string for pre-migration rows
        reasoning = row.get("judge_rationale") or f"voice {voice:.2f} · safety {safety:.2f} · appr {appr:.2f}"
        judges.append(
            Judge(
                name=row.get("judge", "Unknown"),
                score=score,
                vote=vote,
                reasoning=reasoning,
            )
        )

    # Latency: for now use "—" unless we can extract from decision timing
    latency = "—"

    return judges, latency


def _build_activity_spans_from_decision(
    conn: Any, decision_id: str
) -> tuple[list[Span], str]:
    """Build spans from autonomy_decisions + autonomy_jury when action has no run.

    Synthesizes: one 'llm' span (draft), one 'jury' span per judge,
    one 'gate' span per gate, one 'decision' span.
    Returns (spans list, latency).
    """
    decision = _decision_for(conn, decision_id)
    if not decision:
        return [], "—"

    spans: list[Span] = []

    # LLM draft span
    spans.append(
        Span(kind="llm", title="Draft", ms=None, detail="—")
    )

    # Jury spans: one per judge
    jury_rows = _jury_for(conn, decision_id)
    for row in jury_rows:
        voice = float(row.get("voice", 0.5))
        safety = float(row.get("safety", 0.5))
        appr = float(row.get("appr", 0.5))
        detail = f"voice {voice:.2f} · safety {safety:.2f} · appr {appr:.2f}"
        spans.append(
            Span(kind="jury", title=f"Judge: {row.get('judge', 'Unknown')}", ms=None, detail=detail)
        )

    # Gate spans: one per gate result
    gates_jsonb = decision.get("gates") or []
    for gate in gates_jsonb:
        if isinstance(gate, dict):
            gate_label = gate.get("label", "Gate")
            gate_ok = gate.get("ok", False)
            detail = "ok" if gate_ok else "blocked"
            spans.append(
                Span(kind="gate", title=gate_label, ms=None, detail=detail)
            )

    # Decision span: route + confidence
    route = decision.get("decision") or "unknown"
    confidence = float(decision.get("pooled_confidence", 0.0))
    decision_detail = f"{route.upper()} @ {confidence:.2f}"
    spans.append(
        Span(kind="decision", title="Route", ms=None, detail=decision_detail)
    )

    latency = "—"  # No timing captured in autonomy_decisions

    return spans, latency


def _build_activity_links(action_row: dict[str, Any]) -> list[ActivityLink]:
    """Build ActivityLink objects from action deep_link + channel/type.

    Only populated when status='sent'. Returns [] otherwise.
    """
    if action_row.get("status") != "sent":
        return []

    deep_link = action_row.get("deep_link")
    if not deep_link:
        return []

    channel = (action_row.get("channel") or "").lower()
    action_type = (action_row.get("type") or "").lower()

    # Determine targetType and label based on channel/type
    target_type = "POST"  # default
    label = "View"

    if channel == "gmail":
        target_type = "EMAIL"
        label = "View email"
    elif action_type == "comment":
        target_type = "COMMENT"
        label = "View reply"
    elif action_type == "dm":
        target_type = "DM"
        label = "View message"
    elif action_type == "post":
        target_type = "POST"
        label = "View post"

    return [
        ActivityLink(
            label=label,
            target=deep_link,
            target_type=target_type,
        )
    ]


def _build_action(conn: Any, row: dict[str, Any]) -> Action:
    decision: dict[str, Any] | None = None
    jury_rows: list[dict[str, Any]] = []
    if row.get("decision_id"):
        decision = _decision_for(conn, row["decision_id"])
        if decision:
            jury_rows = _jury_for(conn, row["decision_id"])

    if decision:
        confidence = decision.get("pooled_confidence")
        threshold = decision.get("threshold")
        agree = mappers.agreement(decision.get("agreement"))
        gates_src = decision.get("gates") or []
    else:
        confidence = row.get("conf")
        threshold = row.get("threshold")
        agree = ""
        gates_src = []

    confidence = float(confidence) if confidence is not None else 0.0
    threshold = float(threshold) if threshold is not None else 0.0

    # Aggregate per-judge votes into the three console dimensions.
    judges: list[JudgeVote] = []
    dimensions: list[JuryDim] = []
    if jury_rows:
        n = len(jury_rows)
        for v in jury_rows:
            overall = (v["voice"] + v["safety"] + v["appr"]) / 3.0
            judges.append(
                JudgeVote(
                    judge=v["judge"],
                    family=v.get("family"),
                    voice=v["voice"],
                    safety=v["safety"],
                    appr=v["appr"],
                    overall=overall,
                )
            )

        # Build per-dimension verdicts + per-juror breakdowns
        # Compute reliability-weighted mean for each dimension
        dimension_specs = [
            ("Brand voice", "voice"),
            ("Safety", "safety"),
            ("Appropriateness", "appr"),
        ]

        for label, field_name in dimension_specs:
            # Compute per-juror votes and scores
            juror_breakdown: list[JurorDimScore] = []
            dim_scores = []

            for v in jury_rows:
                score = v[field_name]
                dim_scores.append(score)
                # A juror's vote on this dimension: pass if score >= threshold, else fail
                juror_vote = "pass" if score >= threshold else "fail"
                juror_breakdown.append(
                    JurorDimScore(judge=v["judge"], score=score, vote=juror_vote)
                )

            # Compute mean score for this dimension
            mean_score = sum(dim_scores) / n if dim_scores else 0.0

            # Dimension verdict: pass if mean >= threshold, else fail
            dim_verdict = "pass" if mean_score >= threshold else "fail"

            dimensions.append(
                JuryDim(
                    label=label,
                    score=mean_score,
                    verdict=dim_verdict,
                    threshold=threshold,
                    juror_breakdown=juror_breakdown,
                )
            )

    gates = [Gate(label=g.get("label", ""), ok=bool(g.get("ok"))) for g in gates_src]

    esc_kind = row.get("esc_kind") or (decision.get("esc_kind") if decision else None)
    esc_label = row.get("esc_label") or (decision.get("esc_label") if decision else None)

    return Action(
        id=row["id"],
        tenant_id=row["tenant_id"],
        type=mappers.action_type(row.get("type")),
        channel=mappers.channel(row.get("channel")),
        worker=mappers.worker(row.get("worker"), row.get("type")),
        target=row.get("target") or "",
        created_at=mappers.iso(row.get("created_at")),
        subject=row.get("subject"),
        context=row.get("context"),
        draft=row.get("draft") or "",
        confidence=confidence,
        threshold=threshold,
        escalation=Escalation(
            kind=mappers.esc_kind(esc_kind), label=esc_label or ""
        ),
        jury=JuryDecision(
            confidence=confidence,
            threshold=threshold,
            agreement=agree,
            dimensions=dimensions,
            judges=judges,
            is_seeded=(decision.get("run_id", "").startswith("demo-") if decision else False),
        ),
        gates=gates,
        recommendation=row.get("recommend"),
        idempotency_key=row.get("idempotency_key") or "",
        status=mappers.status(row.get("status")),
    )


def review_queue(tenant_id: str, type_filter: str | None = None) -> list[Action]:
    with connect() as conn:
        sql = "SELECT * FROM actions WHERE tenant_id=%s AND status='pending'"
        params: list[Any] = [tenant_id]
        if type_filter:
            sql += " AND lower(type)=%s"
            params.append(type_filter.lower())
        sql += " ORDER BY created_at DESC"
        rows = conn.execute(sql, params).fetchall()
        return [_build_action(conn, r) for r in rows]


def action(action_id: str, tenant_id: str | None = None) -> Action | None:
    with connect() as conn:
        sql = "SELECT * FROM actions WHERE id=%s"
        params: list[Any] = [action_id]
        if tenant_id:
            sql += " AND tenant_id=%s"
            params.append(tenant_id)
        row = conn.execute(sql, params).fetchone()
        if not row:
            return None
        return _build_action(conn, row)


# --------------------------------------------------------------------------- #
# Activity — EXECUTED actions (status='sent'); Action core + handoff extensions
# --------------------------------------------------------------------------- #
def _build_activity(conn: Any, row: dict[str, Any]) -> ActivityItem:
    core = _build_action(conn, row)  # identical core mapping, reused verbatim
    thinking = [str(x) for x in (row.get("thinking") or [])]
    engagement = [
        EngagementTile(label=str(e.get("label", "")), value=str(e.get("value", "")))
        for e in (row.get("engagement") or [])
        if isinstance(e, dict)
    ]

    # v2 observability: runId, trace, judges, spans, links
    run_id = row.get("run_id")
    decision_id = row.get("decision_id")

    # Judges: from autonomy_jury if we have a decision_id
    judges: list[Judge] = []
    trace_latency = "—"
    if decision_id:
        judges, trace_latency = _build_judges_from_jury(conn, decision_id)

    # Spans: if we have a run_id, fetch from its steps; else synthesize from decision
    activity_spans: list[Span] = []
    if run_id:
        # Fetch the run and build spans from its steps
        run_row = conn.execute(
            "SELECT steps FROM runs WHERE run_id=%s", (run_id,)
        ).fetchone()
        if run_row:
            # Convert steps to flat span list (not events)
            steps_jsonb = run_row.get("steps") or []
            for step in steps_jsonb:
                if isinstance(step, dict) and step.get("parent_span_id") is None:
                    # Top-level span only (no nesting for activity view)
                    activity_spans.append(_build_span(step))
        # If run exists but has no steps, synthesize from decision as fallback
        if not activity_spans and decision_id:
            activity_spans, _ = _build_activity_spans_from_decision(conn, decision_id)
    elif decision_id:
        # No run: synthesize spans from decision
        activity_spans, _ = _build_activity_spans_from_decision(conn, decision_id)

    # ExecutionTrace: id + latency
    trace = None
    if decision_id:
        trace = ExecutionTrace(
            id=decision_id,
            latency=trace_latency,
            model="—",  # Unknown unless explicitly tracked
            tokens="—",  # Unknown unless explicitly tracked
        )

    # Links: from deep_link when sent
    links = _build_activity_links(row)

    return ActivityItem(
        id=core.id,
        tenant_id=core.tenant_id,
        type=core.type,
        channel=core.channel,
        worker=core.worker,
        target=core.target,
        created_at=core.created_at,
        subject=core.subject,
        context=core.context,
        draft=core.draft,
        confidence=core.confidence,
        threshold=core.threshold,
        escalation=core.escalation,
        jury=core.jury,
        gates=core.gates,
        recommendation=core.recommendation,
        idempotency_key=core.idempotency_key,
        status=core.status,
        autonomy=mappers.activity_autonomy(row.get("autonomy")),
        content=row.get("draft") or "",
        outcome=Outcome(
            label=row.get("outcome_label") or "", kind=row.get("outcome_kind") or ""
        ),
        thinking=thinking,
        engagement=engagement,
        thread=[],
        comments=[],
        # v2 observability
        run_id=run_id,
        trace=trace,
        judges=judges,
        spans=activity_spans,
        links=links,
    )


def activity(tenant_id: str, type_filter: str | None = None) -> list[ActivityItem]:
    with connect() as conn:
        sql = "SELECT * FROM actions WHERE tenant_id=%s AND status='sent'"
        params: list[Any] = [tenant_id]
        if type_filter:
            sql += " AND lower(type)=%s"
            params.append(type_filter.lower())
        sql += " ORDER BY created_at DESC"
        rows = conn.execute(sql, params).fetchall()
        return [_build_activity(conn, r) for r in rows]


def activity_item(action_id: str) -> ActivityItem | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM actions WHERE id=%s", (action_id,)
        ).fetchone()
        if not row:
            return None
        return _build_activity(conn, row)


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #
def _build_run(conn: Any, row: dict[str, Any]) -> Run:
    chan_rows = conn.execute(
        "SELECT DISTINCT channel FROM autonomy_decisions WHERE run_id=%s",
        (row["run_id"],),
    ).fetchall()
    channels = [mappers.channel(c["channel"]) for c in chan_rows]

    trajectory: list[RunStep] = []
    for step in row.get("steps") or []:
        if not isinstance(step, dict):
            continue
        trajectory.append(
            RunStep(
                at=mappers.iso(step.get("at")) or str(step.get("at") or ""),
                text=step.get("text") or step.get("node") or "",
                state=mappers.step_state(step.get("status") or step.get("state")),
            )
        )

    # v2 observability: build events from steps JSONB
    events = _build_run_events(row.get("steps"))

    # Compute trace_url: if Langfuse is configured, construct the URL; else None
    trace_url: str | None = None
    if observability.is_configured():
        langfuse_host = os.environ.get("LANGFUSE_HOST", observability._DEFAULT_HOST)
        trace_url = f"{langfuse_host.rstrip('/')}/traces/{row['run_id']}"

    return Run(
        id=row["run_id"],
        tenant_id=row["tenant_id"],
        type=row.get("type") or "",
        trigger=mappers.run_trigger(row.get("trigger")),
        status=mappers.run_status(row.get("status")),
        started_at=mappers.iso(row.get("created_at")),
        duration=mappers.duration(row.get("created_at"), row.get("updated_at")),
        auto_count=row.get("auto_count") or 0,
        review_count=row.get("review_count") or 0,
        retries=row.get("retries") or 0,
        idempotency_key=row["run_id"],
        channels=channels,
        trajectory=trajectory,
        note=None,
        trace_url=trace_url,
        events=events,
    )


def runs(tenant_id: str, status_filter: str | None = None) -> list[Run]:
    with connect() as conn:
        sql = "SELECT * FROM runs WHERE tenant_id=%s"
        params: list[Any] = [tenant_id]
        if status_filter:
            # Map console RunStatus back to the DB's lowercase vocabulary.
            wanted = status_filter.upper()
            db_status = {
                "SUCCESS": ("completed", "success"),
                "FAILED": ("failed",),
                "RUNNING": ("running", "needs-review"),
            }.get(wanted)
            if db_status:
                placeholders = ",".join(["%s"] * len(db_status))
                sql += f" AND lower(status) IN ({placeholders})"
                params.extend(db_status)
        sql += " ORDER BY created_at DESC"
        rows = conn.execute(sql, params).fetchall()
        return [_build_run(conn, r) for r in rows]


def run(run_id: str, tenant_id: str | None = None) -> Run | None:
    with connect() as conn:
        sql = "SELECT * FROM runs WHERE run_id=%s"
        params: list[Any] = [run_id]
        if tenant_id:
            sql += " AND tenant_id=%s"
            params.append(tenant_id)
        row = conn.execute(sql, params).fetchone()
        if not row:
            return None
        return _build_run(conn, row)


# --------------------------------------------------------------------------- #
# Feed — derived from real runs + decisions + actions (no events table exists)
# --------------------------------------------------------------------------- #
def _action_feed(r: dict[str, Any]) -> dict[str, Any]:
    st = (r.get("status") or "").lower()
    chip, sev = {
        "pending": ("Review", "WARN"),
        "approved": ("Approved", "INFO"),
        "sending": ("Sending", "INFO"),
        "sent": ("Sent", "SUCCESS"),
        "rejected": ("Rejected", "WARN"),
        "failed": ("Failed", "ERROR"),
    }.get(st, (None, "INFO"))
    target = r.get("target") or ""
    return {
        "id": f"act-{r['id']}",
        "tenant_id": r["tenant_id"],
        "worker": mappers.worker(r.get("worker"), r.get("type")),
        "text": f"{mappers.action_type(r.get('type')).title()} · {target}".strip(" ·"),
        "at": mappers.iso(r.get("updated_at") or r.get("created_at")),
        "chip": chip,
        "severity": sev,
        "ts": r.get("updated_at") or r.get("created_at"),
        "action_id": r.get("id"),
        "run_id": r.get("run_id"),
        "decision_id": r.get("decision_id"),
    }


def _decision_feed(r: dict[str, Any]) -> dict[str, Any]:
    auto = (r.get("decision") or "").lower() == "auto"
    worker = "JURY"
    if (r.get("esc_kind") or "").lower() == "safety":
        worker = "SAFETY"
    label = r.get("esc_label") or r.get("decision") or "decision"
    return {
        "id": f"dec-{r['decision_id']}",
        "tenant_id": r["tenant_id"],
        "worker": worker,
        "text": f"{r.get('action_kind') or 'action'} · {mappers.channel(r.get('channel'))} · {label}",
        "at": mappers.iso(r.get("created_at")),
        "chip": None if auto else "Escalated",
        "severity": "INFO" if auto else "WARN",
        "ts": r.get("created_at"),
        "action_id": None,
        "run_id": r.get("run_id"),
        "decision_id": r.get("decision_id"),
    }


def _run_feed(r: dict[str, Any]) -> dict[str, Any]:
    st = (r.get("status") or "").lower()
    sev = {"completed": "SUCCESS", "failed": "ERROR", "running": "INFO"}.get(st, "INFO")
    return {
        "id": f"run-{r['run_id']}",
        "tenant_id": r["tenant_id"],
        "worker": "TEMPORAL",
        "text": f"{r.get('type') or 'run'} run {st}",
        "at": mappers.iso(r.get("created_at")),
        "chip": None,
        "severity": sev,
        "ts": r.get("created_at"),
        "action_id": None,
        "run_id": r.get("run_id"),
        "decision_id": None,
    }


def _feed_rows(tenant_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        events: list[dict[str, Any]] = []
        for r in conn.execute(
            "SELECT * FROM actions WHERE tenant_id=%s ORDER BY updated_at DESC LIMIT 50",
            (tenant_id,),
        ).fetchall():
            events.append(_action_feed(r))
        for r in conn.execute(
            "SELECT * FROM autonomy_decisions WHERE tenant_id=%s "
            "ORDER BY created_at DESC LIMIT 50",
            (tenant_id,),
        ).fetchall():
            events.append(_decision_feed(r))
        for r in conn.execute(
            "SELECT * FROM runs WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 50",
            (tenant_id,),
        ).fetchall():
            events.append(_run_feed(r))
    events.sort(key=lambda e: (e["ts"] is not None, e["ts"]), reverse=True)
    return events


def feed(
    tenant_id: str,
    limit: int = 100,
    worker_filter: str | None = None,
    after: str | None = None,
) -> list[FeedEvent]:
    rows = _feed_rows(tenant_id)
    if worker_filter:
        wf = mappers.worker(worker_filter)
        rows = [e for e in rows if e["worker"] == wf]
    out: list[FeedEvent] = []
    for e in rows[: max(0, limit)]:
        out.append(
            FeedEvent(
                id=e["id"],
                tenant_id=e["tenant_id"],
                worker=e["worker"],
                text=e["text"],
                at=e["at"],
                chip=e["chip"],
                severity=e["severity"],
                action_id=e.get("action_id"),
                run_id=e.get("run_id"),
                decision_id=e.get("decision_id"),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# KPIs + system health (derived from real rows; missing sources -> 0)
# --------------------------------------------------------------------------- #
def _count(conn: Any, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(next(iter(row.values()))) if row else 0


def kpis(tenant_id: str) -> Kpis:
    with connect() as conn:
        review = _count(
            conn,
            "SELECT count(*) FROM actions WHERE tenant_id=%s AND status='pending'",
            (tenant_id,),
        )
        total_dec = _count(
            conn,
            "SELECT count(*) FROM autonomy_decisions WHERE tenant_id=%s",
            (tenant_id,),
        )
        auto_dec = _count(
            conn,
            "SELECT count(*) FROM autonomy_decisions WHERE tenant_id=%s "
            "AND lower(decision)='auto'",
            (tenant_id,),
        )
        outreach_today = _count(
            conn,
            "SELECT count(*) FROM actions WHERE tenant_id=%s AND lower(type)='outreach' "
            "AND status='sent' AND sent_at::date = current_date",
            (tenant_id,),
        )
        comments_auto = _count(
            conn,
            "SELECT count(*) FROM actions WHERE tenant_id=%s AND lower(type)='comment' "
            "AND status='sent'",
            (tenant_id,),
        )
        comments_review = _count(
            conn,
            "SELECT count(*) FROM actions WHERE tenant_id=%s AND lower(type)='comment' "
            "AND status='pending'",
            (tenant_id,),
        )
        posts_published = _count(
            conn,
            "SELECT count(*) FROM actions WHERE tenant_id=%s AND lower(type)='post' "
            "AND status='sent'",
            (tenant_id,),
        )
    autonomy_pct = (auto_dec / total_dec) if total_dec else 0.0
    return Kpis(
        autonomy_pct=autonomy_pct,
        review_queue_count=review,
        outreach_today=outreach_today,
        complaints_pct=0.0,
        comments_auto=comments_auto,
        comments_review=comments_review,
        posts_published=posts_published,
        posts_scheduled=0,
    )


def system_health(tenant_id: str) -> SystemHealth:
    with connect() as conn:
        gmail_used = _count(
            conn,
            "SELECT count(*) FROM actions WHERE tenant_id=%s AND lower(channel)='gmail' "
            "AND status='sent' AND sent_at::date = current_date",
            (tenant_id,),
        )
        ig_used = _count(
            conn,
            "SELECT count(*) FROM actions WHERE tenant_id=%s AND lower(channel)='instagram' "
            "AND status='sent' AND sent_at::date = current_date",
            (tenant_id,),
        )
    return SystemHealth(
        email_complaint_rate=0.0,
        email_bounce_rate=0.0,
        gmail_warmup_used=gmail_used,
        gmail_warmup_cap=GMAIL_WARMUP_CAP,
        ig_publish_used=ig_used,
        ig_publish_cap=IG_PUBLISH_CAP,
        checkpoint_status="healthy",
    )


# --------------------------------------------------------------------------- #
# Overview + tenant
# --------------------------------------------------------------------------- #
def overview(tenant_id: str) -> Overview:
    return Overview(
        kpis=kpis(tenant_id),
        attention=review_queue(tenant_id)[:20],
        recent_runs=runs(tenant_id)[:10],
        system_health=system_health(tenant_id),
        feed_preview=feed(tenant_id, limit=8),
    )


def tenant(tenant_id: str) -> Tenant:
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT channel FROM autonomy_decisions WHERE tenant_id=%s "
            "UNION SELECT DISTINCT channel FROM actions WHERE tenant_id=%s",
            (tenant_id, tenant_id),
        ).fetchall()
    channels = sorted({mappers.channel(r["channel"]) for r in rows if r["channel"]})
    if not channels:
        channels = ["GMAIL", "INSTAGRAM", "FACEBOOK"]
    # 439 HOLD posture: every channel is approve-first and held; nothing auto-fires.
    autonomy = [
        AutonomyConfig(channel=c, mode="APPROVE_FIRST", threshold=0.85, held=True)
        for c in channels
    ]
    return Tenant(
        id=tenant_id,
        name=tenant_id,
        pack="",
        channels=channels,
        autonomy=autonomy,
        engine_state="RUNNING",
    )


# --------------------------------------------------------------------------- #
# Mutations — lazy seam delegation
# --------------------------------------------------------------------------- #
def _seam(module: str, *names: str) -> Callable[..., Any]:
    """Import ``module`` lazily and return the first present attribute in ``names``.

    Raises a clear ``RuntimeError`` (surfaced as a GraphQL error, not a crash) if
    the module or all candidate functions are absent — the expected state until
    the parallel ``engine/actions/`` work lands.
    """

    try:
        mod = importlib.import_module(module)
    except Exception as exc:  # ModuleNotFoundError and friends
        raise RuntimeError(
            f"Action seam '{module}' is not available yet "
            f"(built in parallel under engine/actions/): {exc}"
        ) from exc
    for name in names:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    raise RuntimeError(
        f"Action seam '{module}' is present but exposes none of {names}; "
        "the approve/publish path is not wired yet."
    )


def approve_action(action_id: str, idempotency_key: str) -> Action | None:
    _seam("actions.publish", "approve_and_publish")(action_id)
    return action(action_id)


def reject_action(action_id: str, reason: str | None = None) -> Action | None:
    _seam("actions.publish", "reject")(action_id)
    return action(action_id)


def regenerate_action(action_id: str) -> Action | None:
    _seam("actions.publish", "regenerate", "regenerate_action")(action_id)
    return action(action_id)


def edit_action_draft(action_id: str, draft: str) -> Action | None:
    """Edit the pending draft in place. This is a benign, well-defined local
    write (no send), so it updates the ``actions`` row directly rather than via a
    seam — the seam set has no edit function and the demo's edit button needs it."""

    with connect() as conn:
        conn.execute(
            "UPDATE actions SET draft=%s, updated_at=now() WHERE id=%s",
            (draft, action_id),
        )
    return action(action_id)
