"""Real campaign state for the voice supervisor (CustomerAcq-6bv / 65w.12).

ONE credit-INDEPENDENT source of truth the voice supervisor answers from — the SAME
ordered rows the frontend review queue renders — so voice and the frontend can never
disagree about "draft #1", the draft count, or which agent ran.

The reproduced bug: the voice model GUESSED (named a different lead than the frontend
for draft #1, said "2 drafts" when 7 existed). This layer removes the guessing:

  * ``build_campaign_state`` — PURE over already-loaded DB rows: draft #1 is the real
    first lead by creation order (== the review-queue order), counts come from real
    action rows, and per-agent status reflects the real ``agent_runs`` INCLUDING the
    fail-closed ``failed`` status (0dy). No model / ANTHROPIC key involved.
  * ``campaign_state`` — thin loader that reads those rows from Postgres.
  * ``describe_draft`` / ``describe_state`` — plain-language, TRUTHFUL narration the
    voice supervisor speaks (honest "no draft #N" when out of range, never invented).

Live voice AUDIO still needs the realtime key; these STATE answers do not.
"""

from __future__ import annotations

import json
from typing import Any

from studio.campaign_runner import (
    campaign_run_status,
    derive_agent_statuses,
    required_step_failures,
)

# The canonical spine roles the supervisor reports on, in execution order.
_REPORT_ROLES = ("planner", "researcher", "analyst", "strategist", "draft", "critic", "jury")


def _get(row: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a dict OR a dataclass-style row (ActionRow) uniformly."""
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _cust_of(idempotency_key: str | None) -> str | None:
    """The customer id encoded in a staged draft's idempotency key ``{run_id}:{cust}``."""
    if not idempotency_key or ":" not in idempotency_key:
        return None
    return idempotency_key.rsplit(":", 1)[-1]


def _parse_context(context: Any) -> dict[str, Any]:
    if isinstance(context, dict):
        return context
    if isinstance(context, str) and context.strip():
        try:
            val = json.loads(context)
            return val if isinstance(val, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _strategy_angle(agent_runs: list[dict[str, Any]]) -> str | None:
    """The campaign angle the (successful) strategist set, or None if it never landed
    one (e.g. the strategist failed — then drafts fell back to the base goal)."""
    for ar in agent_runs:
        if str(ar.get("role") or "").lower() != "strategist":
            continue
        out = ar.get("output") or {}
        if not isinstance(out, dict) or out.get("status") == "failed":
            continue
        for k in ("target_angle", "primary_angle", "angle", "big_idea"):
            v = out.get(k)
            if v:
                return str(v)
    return None


def _index_by_customer(agent_runs: list[dict[str, Any]], role: str) -> dict[str, dict[str, Any]]:
    """First recorded ``role`` run per customer id (from its input.customer_id)."""
    out: dict[str, dict[str, Any]] = {}
    for ar in agent_runs:
        if str(ar.get("role") or "").lower() != role:
            continue
        cust = str((ar.get("input") or {}).get("customer_id") or "")
        if cust and cust not in out:
            out[cust] = ar.get("output") or {}
    return out


def _jury_result(agent_runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for ar in reversed(agent_runs):  # last jury record is the run-level verdict
        if str(ar.get("role") or "").lower() == "jury":
            out = ar.get("output") or {}
            return {
                "decision": out.get("decision"),
                "aggregate": out.get("aggregate"),
                "blocked": out.get("decision") == "blocked" or out.get("status") == "failed",
            }
    return None


def build_campaign_state(
    *,
    run_id: str,
    action_rows: list[Any],
    agent_runs: list[dict[str, Any]],
    run_status: str | None = None,
) -> dict[str, Any]:
    """Assemble the campaign's REAL state from already-loaded rows (pure; no DB, no model).

    ``action_rows`` are the run's staged draft actions, OLDEST FIRST (== the review-queue
    order the frontend renders); ``agent_runs`` are the per-role runs, oldest first;
    ``run_status`` is the DB ``runs.status`` when known (else derived fail-closed)."""
    # Fail-closed terminal status + honest per-agent status (0dy): strategist/critic that
    # errored read 'failed', never a fake 'done'. Derived from the real agent_runs so it is
    # correct even for a legacy runs row written before the fail-closed fix.
    derived_status = campaign_run_status(agent_runs)
    # Fail-closed wins: a required-gate failure in the real agent_runs reports 'failed'
    # even if a LEGACY runs row (written before the 0dy fix) still says 'completed'. When
    # nothing failed, defer to the authoritative DB status (running / completed).
    status = "failed" if derived_status == "failed" else (run_status or derived_status or "unknown")
    agents = derive_agent_statuses("provided_leads", agent_runs, status)
    failure_summary = required_step_failures(agent_runs)

    strategy_angle = _strategy_angle(agent_runs)
    critic_by_cust = _index_by_customer(agent_runs, "critic")
    research_by_cust = _index_by_customer(agent_runs, "researcher")
    jury_result = _jury_result(agent_runs)

    drafts: list[dict[str, Any]] = []
    counts: dict[str, int] = {"drafts": 0, "pending": 0, "approved": 0, "rejected": 0, "sent": 0}
    for i, row in enumerate(action_rows, start=1):
        cust = _cust_of(_get(row, "idempotency_key"))
        ctx = _parse_context(_get(row, "context"))
        crit = critic_by_cust.get(cust or "", {})
        rsch = research_by_cust.get(cust or "", {})
        review_status = _get(row, "status") or "pending"
        counts["drafts"] += 1
        counts[review_status] = counts.get(review_status, 0) + 1

        skill_used = ctx.get("skill_used")
        why_bits = [b for b in (ctx.get("skill_why"), strategy_angle) if b]
        drafts.append({
            "index": i,
            "id": _get(row, "id"),
            "created_at": str(_get(row, "created_at")) if _get(row, "created_at") is not None else None,
            "lead_name": _get(row, "target"),
            "recipient": _get(row, "target"),
            "channel": _get(row, "channel"),
            "subject": _get(row, "subject"),
            "body": _get(row, "draft"),
            "strategy_used": strategy_angle,
            "skill_used": skill_used,
            "research_used": {"cited": rsch.get("cited"), "sources": rsch.get("sources")} if rsch else None,
            "critic_result": (
                {
                    "verdict": crit.get("verdict"),
                    "confidence": crit.get("confidence"),
                    "rationale": crit.get("rationale"),
                }
                if crit else None
            ),
            "jury_result": jury_result,
            "review_status": review_status,
            "conf": _get(row, "conf"),
            "why_written": "; ".join(str(b) for b in why_bits) or None,
        })

    # The active agent (only meaningful mid-run): the one derive marked 'running'.
    active_agent = next(
        (role for role in _REPORT_ROLES if agents.get(role) == "running"), None
    )

    return {
        "run_id": run_id,
        "status": status,
        "run_status_db": run_status,
        "active_agent": active_agent,
        "agents": agents,
        "failure_summary": failure_summary,
        "counts": counts,
        "expected": None,
        "drafts": drafts,
        "draft_1": drafts[0] if drafts else None,
        "strategy_angle": strategy_angle,
        "jury_result": jury_result,
    }


def campaign_state(run_id: str, *, dsn: str | None = None, run_status: str | None = None) -> dict[str, Any]:
    """Load one run's REAL state from Postgres (credit-independent — DB only).

    Reads the staged draft actions (oldest first) + the per-role agent_runs + the
    ``runs.status``, then assembles them via :func:`build_campaign_state`."""
    from actions.store import list_actions_for_run

    action_rows = list_actions_for_run(run_id, dsn=dsn)
    agent_runs: list[dict[str, Any]] = []
    try:
        from team.store import TeamStore

        ts = TeamStore(dsn)
        ts.setup()
        agent_runs = list(ts.list_agent_runs(run_id))
    except Exception:
        agent_runs = []
    if run_status is None:
        run_status = _load_run_status(run_id, dsn)
    return build_campaign_state(
        run_id=run_id, action_rows=action_rows, agent_runs=agent_runs, run_status=run_status
    )


def _load_run_status(run_id: str, dsn: str | None) -> str | None:
    """Best-effort read of the DB ``runs.status`` (None when unavailable)."""
    try:
        import os

        import psycopg

        resolved = dsn or os.environ.get("ENGINE_DATABASE_URL") \
            or "postgresql://scalers:scalers@localhost:5432/scalers"
        with psycopg.connect(resolved, connect_timeout=5) as conn:
            row = conn.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()
        return str(row[0]).lower() if row else None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Truthful narration the voice supervisor speaks (never fabricated).
# --------------------------------------------------------------------------- #

def describe_draft(state: dict[str, Any], n: int) -> str:
    """A plain-language description of draft #``n`` — the REAL lead, recipient, and why
    it was written. Honest "no draft #N" when out of range (never invents a draft)."""
    drafts = state.get("drafts") or []
    total = len(drafts)
    if n < 1 or n > total:
        return (
            f"There is no draft #{n} — this campaign has {total} "
            f"draft{'s' if total != 1 else ''} so far."
        )
    d = drafts[n - 1]
    parts = [f"Draft #{d['index']} is to {d['lead_name']}"]
    if d.get("channel"):
        parts[-1] += f" on {d['channel']}"
    if d.get("subject"):
        parts.append(f"Subject: {d['subject']}.")
    why = d.get("why_written") or d.get("skill_used") or d.get("strategy_used")
    if why:
        parts.append(f"Written for: {why}.")
    crit = d.get("critic_result") or {}
    if crit.get("verdict"):
        parts.append(f"Critic verdict: {crit['verdict']}.")
    parts.append(f"Review status: {d.get('review_status')}.")
    return " ".join(parts)


def describe_state(state: dict[str, Any]) -> str:
    """A short, truthful spoken summary: how many drafts, which agents ran (incl. an
    honest 'failed'), and the run's terminal status."""
    c = state.get("counts") or {}
    n = c.get("drafts", 0)
    if n == 0:
        return "No drafts have been staged yet for this campaign."
    agents = state.get("agents") or {}
    lines = [f"{n} draft{'s' if n != 1 else ''} staged"]
    pend = c.get("pending", 0)
    if pend:
        lines[-1] += f" ({pend} pending review)"
    # Report the two quality gates the operator asks about most.
    for role in ("strategist", "critic"):
        st = agents.get(role)
        if st:
            lines.append(f"{role} {st}")
    active = state.get("active_agent")
    if active:
        lines.append(f"{active} is working now")
    lines.append(f"run status: {state.get('status')}")
    return "; ".join(lines) + "."
