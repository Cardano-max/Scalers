"""Supervisor fleet board + patrol loop — the initech pattern for marketing agents.

The operator's reference is initech (https://github.com/nmelo/initech): a runtime
where a supervisor drives a fleet of persistent agents with `status` (who's on
what, live activity), `patrol` (bulk peek across every agent), `peek`/`send`/
`interrupt`, and automatic stall surfacing. This module is that runtime for the
campaign agents:

  * :func:`fleet_status`  = ``initech status`` — one row per recent run: current
    role, last-step age, activity classification (``working`` / ``stalled`` /
    ``waiting-operator`` / ``done`` / ``failed``), staged drafts, pending
    directives.
  * :func:`patrol_once`   = ``initech patrol`` — sweep every non-terminal run:
    detect stalls (a "running" run whose agents stopped stepping) and cross-agent
    contradictions (deterministic coherence rules ONLY — a background sweep never
    spends tokens). Every NEW finding is recorded as a ``role='supervisor'``
    agent_run (deduped), so patrol interventions share lineage with every other
    agent step.
  * :func:`start_patrol_loop` = the loop — an asyncio background task the engine
    starts at boot (``SUPERVISOR_PATROL_SECONDS``, default 60; ``0`` disables).

`send`/`interrupt` already exist as the directives channel
(:mod:`studio.supervisor_control`) and `peek` as ``GET /studio/run/{id}`` — this
module deliberately adds NO new mutation power: patrol only OBSERVES and records;
corrections still go through the closed directive set.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

# A run whose agents haven't stepped for this long, while the run row still says
# non-terminal, is surfaced as stalled (initech's yellow "stalled" state).
STALL_AFTER_SECONDS = 300
# Runs older than this are history, not fleet: keep the board readable.
FLEET_WINDOW_MINUTES = 24 * 60

_TERMINAL = {"completed", "failed", "aborted"}


def _dsn(dsn: str | None) -> str:
    return dsn or os.environ.get(
        "ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers"
    )


def _connect(dsn: str | None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), row_factory=dict_row, autocommit=True)


def _classify(status: str, awaiting_selection: bool, last_age_s: float | None) -> str:
    """initech-style activity state from REAL recorded facts only. Terminal
    statuses win over a stale 'awaiting' selection row — a finished run is done
    even if its selection record was never resolved."""
    if status == "failed":
        return "failed"
    if status in _TERMINAL:
        return "done"
    if awaiting_selection:
        return "waiting-operator"
    if last_age_s is None:
        return "starting"
    if last_age_s >= STALL_AFTER_SECONDS:
        return "stalled"
    return "working"


def _inflight_tenant(conn, run_id: str, registry: dict[str, dict]) -> str | None:
    """Attribute an in-flight run (agent_runs steps, no runs row yet) to its REAL
    tenant, from durable evidence first: the run's campaign_blueprints row (written at
    plan time), else its staged actions rows, else the in-process launch registry's
    recorded tenant. None = no real attribution (the run is then NOT shown — never a
    cross-tenant guess)."""
    try:
        row = conn.execute(
            "SELECT tenant_id FROM campaign_blueprints WHERE run_id=%s", (run_id,)
        ).fetchone()
        if row and row.get("tenant_id"):
            return str(row["tenant_id"])
    except Exception:
        pass
    try:
        row = conn.execute(
            "SELECT tenant_id FROM actions WHERE run_id=%s LIMIT 1", (run_id,)
        ).fetchone()
        if row and row.get("tenant_id"):
            return str(row["tenant_id"])
    except Exception:
        pass
    reg = registry.get(run_id) or {}
    t = reg.get("tenant_id")
    return str(t) if t else None


def _inflight_rows(
    conn, tenant_id: str, window_minutes: int, known_run_ids: set[str]
) -> list[dict[str, Any]]:
    """The IN-FLIGHT runs the materialized ``runs`` table cannot see: run_ids whose
    agent_runs stepped inside the window but have NO runs row yet (the studio
    executor writes the runs row ONCE, at completion). Real rows only — steps are
    read from agent_runs; the launch registry / blueprint / actions attribute the
    tenant. A run with no real tenant evidence is excluded, never guessed."""
    try:
        from studio.live_state import get_runs_registry

        registry = get_runs_registry()
    except Exception:
        registry = {}
    try:
        candidates = conn.execute(
            """
            SELECT ar.run_id,
                   min(ar.created_at)                                        AS first_step_at,
                   max(ar.created_at)                                        AS last_step_at,
                   count(*)                                                  AS n_steps,
                   (array_agg(ar.role ORDER BY ar.created_at DESC))[1]       AS last_role,
                   EXTRACT(EPOCH FROM (now() - max(ar.created_at)))          AS last_step_age_s
              FROM agent_runs ar
              LEFT JOIN runs r ON r.run_id = ar.run_id
             WHERE r.run_id IS NULL
               AND ar.created_at > now() - make_interval(mins => %s)
             GROUP BY ar.run_id
             ORDER BY min(ar.created_at) DESC
            """,
            (window_minutes,),
        ).fetchall()
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for c in candidates:
        rid = str(c["run_id"])
        if rid in known_run_ids:
            continue
        if _inflight_tenant(conn, rid, registry) != tenant_id:
            continue
        reg = registry.get(rid) or {}
        status = str(reg.get("status") or "running")
        try:
            n_pending = int(conn.execute(
                "SELECT count(*) FROM actions WHERE run_id=%s AND status='pending'", (rid,)
            ).fetchone()["count"])
        except Exception:
            n_pending = 0
        try:
            awaiting = bool(conn.execute(
                "SELECT EXISTS (SELECT 1 FROM artwork_selections s "
                "WHERE s.run_id=%s AND s.status='awaiting')", (rid,)
            ).fetchone()["exists"])
        except Exception:
            awaiting = False
        age = float(c["last_step_age_s"]) if c["last_step_age_s"] is not None else None
        rows.append(
            {
                "run_id": rid,
                "status": status,
                "type": "campaign",
                "activity": _classify(status, awaiting, age),
                "last_role": c["last_role"],
                "last_step_age_s": round(age, 1) if age is not None else None,
                "n_steps": int(c["n_steps"]),
                "n_pending_drafts": n_pending,
                "n_pending_directives": 0,
                "n_applied_directives": 0,
                "created_at": c["first_step_at"].isoformat(),
                # Honest marker: this run is EXECUTING (or just finished) and has no
                # materialized runs row yet — its fields come from agent_runs + the
                # live launch registry, not from the runs table.
                "in_flight": True,
            }
        )
    return rows


def fleet_status(
    tenant_id: str,
    *,
    dsn: str | None = None,
    window_minutes: int = FLEET_WINDOW_MINUTES,
) -> list[dict[str, Any]]:
    """``initech status`` — one honest row per recent run of this tenant. Every
    field is read from the runs/agent_runs/actions/run_directives tables; nothing
    is inferred beyond the activity classification (whose inputs are shown).

    IN-FLIGHT runs are included too (``in_flight: true``): the studio executor only
    materializes a ``runs`` row at COMPLETION, so an executing run was previously
    invisible to this board. Those rows are read from the run's own live
    ``agent_runs`` steps (the incrementally-written source GET /studio/run/{id}
    polls) and attributed to the tenant via blueprint/actions/launch-registry
    evidence — never a fabricated status."""
    with _connect(dsn) as conn:
        rows = conn.execute(
            """
            SELECT r.run_id,
                   r.status,
                   r.type,
                   r.created_at,
                   r.updated_at,
                   (SELECT ar.role FROM agent_runs ar WHERE ar.run_id = r.run_id
                     ORDER BY ar.created_at DESC LIMIT 1)                        AS last_role,
                   (SELECT EXTRACT(EPOCH FROM (now() - max(ar.created_at)))
                      FROM agent_runs ar WHERE ar.run_id = r.run_id)             AS last_step_age_s,
                   (SELECT count(*) FROM agent_runs ar WHERE ar.run_id = r.run_id) AS n_steps,
                   (SELECT count(*) FROM actions a
                     WHERE a.run_id = r.run_id AND a.status = 'pending')         AS n_pending_drafts,
                   (SELECT count(*) FROM run_directives d
                     WHERE d.run_id = r.run_id AND d.status = 'pending')         AS n_pending_directives,
                   (SELECT count(*) FROM run_directives d
                     WHERE d.run_id = r.run_id AND d.status = 'applied')         AS n_applied_directives,
                   EXISTS (SELECT 1 FROM artwork_selections s
                            WHERE s.run_id = r.run_id AND s.status = 'awaiting') AS awaiting_selection
              FROM runs r
             WHERE r.tenant_id = %s
               AND r.created_at > now() - make_interval(mins => %s)
             ORDER BY r.created_at DESC
            """,
            (tenant_id, window_minutes),
        ).fetchall()
        board: list[dict[str, Any]] = []
        for r in rows:
            age = float(r["last_step_age_s"]) if r["last_step_age_s"] is not None else None
            # The artwork gate's mid-run pause is recorded in artwork_selections
            # (status='awaiting'), not on the run row — read it from there.
            awaiting = bool(r["awaiting_selection"])
            board.append(
                {
                    "run_id": r["run_id"],
                    "status": r["status"],
                    "type": r["type"],
                    "activity": _classify(str(r["status"]), awaiting, age),
                    "last_role": r["last_role"],
                    "last_step_age_s": round(age, 1) if age is not None else None,
                    "n_steps": int(r["n_steps"]),
                    "n_pending_drafts": int(r["n_pending_drafts"]),
                    "n_pending_directives": int(r["n_pending_directives"]),
                    "n_applied_directives": int(r["n_applied_directives"]),
                    "created_at": r["created_at"].isoformat(),
                    "in_flight": False,
                }
            )
        # UNION the runs that are EXECUTING right now (steps landing in agent_runs,
        # runs row not yet written) so the supervisor board is never blind mid-run.
        board.extend(
            _inflight_rows(conn, tenant_id, window_minutes, {b["run_id"] for b in board})
        )
    board.sort(key=lambda b: str(b.get("created_at") or ""), reverse=True)
    return board


def _already_patrolled(conn, run_id: str, rule: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM agent_runs WHERE run_id=%s AND role='supervisor' "
        "AND input->>'patrol' = %s LIMIT 1",
        (run_id, rule),
    ).fetchone()
    return row is not None


def _record_patrol_step(conn, run_id: str, rule: str, detail: dict[str, Any]) -> None:
    from psycopg.types.json import Json

    conn.execute(
        "INSERT INTO agent_runs (id, campaign_id, run_id, role, model, input, output) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
        (
            "agr_" + uuid.uuid4().hex[:16],
            run_id.split("-")[0] if "-" in run_id else run_id,
            run_id,
            "supervisor",
            "patrol:deterministic",
            Json({"patrol": rule}),
            Json(detail),
        ),
    )


def patrol_once(tenant_id: str, *, dsn: str | None = None) -> dict[str, Any]:
    """``initech patrol`` — one sweep over every non-terminal recent run. Detects
    stalls and cross-agent contradictions (deterministic rules only; zero tokens)
    and records each NEW finding as a ``role='supervisor'`` agent_run so the live
    panel shows the supervisor noticing — exactly once per (run, rule)."""
    from studio.supervisor_control import review_run_coherence

    board = fleet_status(tenant_id, dsn=dsn)
    findings: list[dict[str, Any]] = []
    with _connect(dsn) as conn:
        for row in board:
            rid = row["run_id"]
            if row["activity"] == "stalled" and not _already_patrolled(conn, rid, "stalled"):
                detail = {
                    "note": (
                        f"run stalled: no agent step for {row['last_step_age_s']}s "
                        f"(last role: {row['last_role']}); status still {row['status']!r}"
                    ),
                    "suggest": "steer_run pause/abort, or re-dispatch the campaign",
                }
                _record_patrol_step(conn, rid, "stalled", detail)
                findings.append({"run_id": rid, "rule": "stalled", **detail})
            if row["activity"] == "waiting-operator" and not _already_patrolled(
                conn, rid, "waiting-operator"
            ):
                detail = {
                    "note": "run is paused for an operator artwork choice (top-4 staged)",
                    "suggest": "open the run and select an artwork to resume",
                }
                _record_patrol_step(conn, rid, "waiting-operator", detail)
                findings.append({"run_id": rid, "rule": "waiting-operator", **detail})
            # Coherence rules only make sense once agents actually produced output,
            # and only need one look per run once it's finished stepping.
            if row["n_steps"] >= 3 and row["activity"] in ("working", "stalled"):
                verdict = review_run_coherence(rid, tenant_id, dsn=dsn, llm=False)
                for f in verdict.get("findings") or []:
                    rule = f"coherence:{f['rule']}"
                    if _already_patrolled(conn, rid, rule):
                        continue
                    detail = {"note": f["detail"], "suggest": f["suggest"]}
                    _record_patrol_step(conn, rid, rule, detail)
                    findings.append({"run_id": rid, "rule": rule, **detail})
    return {
        "tenant_id": tenant_id,
        "swept": len(board),
        "active": sum(1 for r in board if r["activity"] in ("working", "starting")),
        "stalled": sum(1 for r in board if r["activity"] == "stalled"),
        "waiting_operator": sum(1 for r in board if r["activity"] == "waiting-operator"),
        "new_findings": findings,
    }


async def start_patrol_loop(tenant_id: str, *, dsn: str | None = None) -> None:
    """The loop: patrol forever at ``SUPERVISOR_PATROL_SECONDS`` (default 60s;
    ``0`` disables). Errors are swallowed per-tick — a broken sweep must never
    take the engine down — but each tick's summary is printed to the engine log
    so the patrol itself is observable."""
    import asyncio

    try:
        interval = float(os.environ.get("SUPERVISOR_PATROL_SECONDS", "60") or 0)
    except ValueError:
        interval = 60.0
    if interval <= 0:
        return
    while True:
        try:
            summary = await asyncio.to_thread(patrol_once, tenant_id, dsn=dsn)
            if summary["new_findings"]:
                print(f"[supervisor-patrol] {json.dumps(summary, default=str)}", flush=True)
        except Exception as exc:  # noqa: BLE001 — the loop must survive any tick
            print(f"[supervisor-patrol] tick failed: {type(exc).__name__}: {exc}", flush=True)
        await asyncio.sleep(interval)
