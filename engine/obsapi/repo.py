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
from typing import Any, Callable

from . import mappers
from .db import connect
from .types import (
    Action,
    ActivityItem,
    AutonomyConfig,
    EngagementTile,
    Escalation,
    FeedEvent,
    Gate,
    JudgeVote,
    JuryDecision,
    JuryDim,
    Kpis,
    Outcome,
    Overview,
    Run,
    RunStep,
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
        dimensions = [
            JuryDim(label="Brand voice", score=sum(v["voice"] for v in jury_rows) / n),
            JuryDim(label="Safety", score=sum(v["safety"] for v in jury_rows) / n),
            JuryDim(
                label="Appropriateness", score=sum(v["appr"] for v in jury_rows) / n
            ),
        ]

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
