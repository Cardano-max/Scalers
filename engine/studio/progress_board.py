"""The structured run-state board (P1.5 blueprint #3) + hard-gated progress-aware replan.

``build_progress_context`` in :mod:`studio.agui` renders a TEXTUAL per-turn view of a run.
This module gives the same run a STRUCTURED board — computed ON DEMAND from the SAME three
real store seams that textual view reads (runs / agent_runs / actions), so there is ONE
counting path and NO mutable board table (the board is derived, never a second source of
truth). The replan is the only durable P1.5 write, and it is an idempotent ``agent_run``
event (deterministic id + the store's ON CONFLICT DO NOTHING), never a table.

  * :class:`ProgressBoard` — known / missing / lead counts / objections_addressed /
    contradictions / channels_complete.
  * :func:`resolve_active_run` — shared run-resolution (factored out of build_progress_
    context so both consume ONE implementation).
  * :func:`compute_board` — pure counting over already-read rows (unit-testable, no DB).
  * :func:`compute_progress_board` — the SINGLE on-demand read path (3 seams → board).
  * :func:`maybe_replan` — the replan trigger. Returns a concrete :class:`PlanDelta` ONLY
    when ALL gates hold (sample ≥ MIN_SAMPLE, margin ≥ MIN_MARGIN, measured ≠ assumed,
    replans_so_far < REPLAN_CAP) AND the delta is non-empty (from ≠ to). A no-diff /
    decorative "replanned" note is IMPOSSIBLE. One noisy analyst read cannot thrash the plan.
  * :func:`replan_event_id` — deterministic id (sha of run_id+assumed+measured+sample_n) so
    a re-run records the replan AT MOST ONCE (exactly-once via ON CONFLICT DO NOTHING).

HONESTY: every field traces to a real row. No run → an honest empty board.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

# Replan gates — deliberately conservative so a single noisy read cannot thrash the plan.
MIN_SAMPLE = 2   # need at least this many grounded-objection reads before replanning
MIN_MARGIN = 1   # the measured winner must beat the runner-up by at least this many reads
REPLAN_CAP = 1   # at most this many replans per run


class ProgressBoard(BaseModel):
    """The structured state of one campaign run — derived, never a stored source of truth."""

    run_id: str | None = None
    run_status: str = "none"
    known: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    leads_total: int = 0
    leads_done: int = 0
    # Objections the analyst measured (stated/inferred) for leads that produced a STAGED
    # (HELD) draft. Named "addressed" — nothing sends in P1.5, so "resolved" would
    # fabricate an outcome.
    objections_addressed: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    channels_complete: list[str] = Field(default_factory=list)


class PlanDelta(BaseModel):
    """The CONCRETE adjustment a replan makes — non-empty by construction (``from_objection
    != to_objection``), so a decorative no-diff replan cannot exist."""

    from_objection: str
    to_objection: str
    new_offer_code: str | None = None
    new_angle: str | None = None
    reason: str = ""


def resolve_active_run(runs: list[Any], actions_all: list[Any]) -> tuple[str | None, Any]:
    """Resolve the ACTIVE run + its runs-row record from already-read rows.

    The latest ``runs`` row is authoritative, BUT a studio run materializes its runs row
    only at the END — so an in-flight run shows up in ``actions`` first. If the newest
    action points at a run with no runs row yet, surface THAT in-flight run (record=None).
    Factored out of ``build_progress_context`` so both share one implementation."""
    record = runs[0] if runs else None
    run_id = getattr(record, "run_id", None) if record is not None else None
    latest_action_rid = next(
        (a.run_id for a in actions_all if getattr(a, "run_id", None)), None
    )
    if (
        latest_action_rid
        and latest_action_rid != run_id
        and not any(getattr(r, "run_id", None) == latest_action_rid for r in runs)
    ):
        return latest_action_rid, None
    return run_id, record


def _run_status(record: Any) -> str:
    if record is None:
        return "running"  # in-flight: the runs row is not materialized yet
    return getattr(getattr(record, "status", None), "value", None) or str(
        getattr(record, "status", "") or "unknown"
    )


def _objection_of(analyst_output: dict[str, Any]) -> str | None:
    """The REAL objection an analyst run measured (stated/inferred only). ``None`` when the
    signal was insufficient — never counted."""
    if not isinstance(analyst_output, dict):
        return None
    signal = (analyst_output.get("objection_signal") or "").strip().lower()
    value = (analyst_output.get("primary_objection") or "").strip().lower()
    if signal in ("stated", "inferred") and value and value != "none-found":
        return value
    return None


def _customer_of(ar: dict[str, Any]) -> str | None:
    inp = ar.get("input")
    return (inp or {}).get("customer_id") if isinstance(inp, dict) else None


def _measured_objections(agent_runs: list[dict[str, Any]]) -> list[str]:
    return [
        o
        for o in (
            _objection_of(ar.get("output"))
            for ar in agent_runs
            if ar.get("role") == "analyst"
        )
        if o
    ]


def dominant_measured_objection(agent_runs: list[dict[str, Any]]) -> str | None:
    """The objection the analyst measured MOST across the run's leads (or ``None``)."""
    objections = _measured_objections(agent_runs)
    if not objections:
        return None
    return Counter(objections).most_common(1)[0][0]


def compute_board(
    run_id: str | None,
    record: Any,
    agent_runs: list[dict[str, Any]],
    run_actions: list[Any],
    plan: Any,
    *,
    contradictions: list[str] | None = None,
) -> ProgressBoard:
    """Pure counting → a :class:`ProgressBoard`. No DB. Every field from a real row."""
    if not run_id:
        return ProgressBoard(
            run_status="none",
            missing=["No campaign run is in flight — nothing has been planned or drafted."],
        )

    drafts = [ar for ar in agent_runs if ar.get("role") == "draft"]
    analysts = [ar for ar in agent_runs if ar.get("role") == "analyst"]
    researchers = [ar for ar in agent_runs if ar.get("role") == "researcher"]
    drafted_customers = {c for c in (_customer_of(ar) for ar in drafts) if c}

    expected = int(
        (getattr(plan, "output_count", 0) or getattr(plan, "lead_count", 0) or 0)
    )
    leads_total = expected or len(researchers) or len(drafts)
    leads_done = len(drafts)

    # objections_addressed: a grounded objection whose lead ALSO produced a STAGED draft
    # (the objection was actually acted on in a HELD draft) — never "resolved" (no send).
    objections_addressed = [
        obj
        for ar in analysts
        for obj in [_objection_of(ar.get("output"))]
        if obj and _customer_of(ar) in drafted_customers
    ]

    per_channel_quota: dict[str, int] = {}
    try:
        from studio.campaign_blueprint import _default_channels, _distribute_quota

        per_channel_quota = _distribute_quota(
            expected, _default_channels(getattr(plan, "channels", None))
        )
    except Exception:
        per_channel_quota = {}
    drafted_by_channel: Counter[str] = Counter(
        str((ar.get("input") or {}).get("channel"))
        for ar in drafts
        if (ar.get("input") or {}).get("channel")
    )
    channels_complete = [
        ch
        for ch, quota in per_channel_quota.items()
        if quota > 0 and drafted_by_channel.get(ch, 0) >= quota
    ]

    known: list[str] = []
    if researchers:
        known.append(f"{len(researchers)} lead(s) researched from real DB history.")
    if analysts:
        known.append(
            f"{len(analysts)} lead(s) psych-analyzed; "
            f"{len(_measured_objections(analysts))} with a grounded objection."
        )
    if drafts:
        known.append(f"{len(drafts)} brand-voiced draft(s) staged HELD (approve-first).")
    n_pending = sum(1 for a in run_actions if getattr(a, "status", None) == "pending")
    if n_pending:
        known.append(f"{n_pending} draft(s) in the Review Queue awaiting approval.")

    missing: list[str] = []
    degraded = sum(
        1 for ar in researchers
        if isinstance(ar.get("output"), dict) and ar["output"].get("degraded")
    )
    if degraded:
        missing.append(f"{degraded} lead(s) had no web research sources (degraded).")
    no_objection = sum(1 for ar in analysts if _objection_of(ar.get("output")) is None)
    if no_objection:
        missing.append(
            f"{no_objection} lead(s) had insufficient signal for a primary objection."
        )
    if expected and leads_done < expected:
        missing.append(f"{expected - leads_done} draft(s) remaining to meet the quota.")

    return ProgressBoard(
        run_id=run_id,
        run_status=_run_status(record),
        known=known,
        missing=missing,
        leads_total=leads_total,
        leads_done=leads_done,
        objections_addressed=objections_addressed,
        contradictions=list(contradictions or []),
        channels_complete=channels_complete,
    )


def _persisted_contradictions(agent_runs: list[dict[str, Any]]) -> list[str]:
    """Contradictions read back from a recorded planner ``replan`` agent_run (the durable
    replan event) — so the board reflects a replan that really happened, not a recompute."""
    out: list[str] = []
    for ar in agent_runs:
        if ar.get("role") != "planner":
            continue
        output = ar.get("output")
        rp = output.get("replan") if isinstance(output, dict) else None
        if isinstance(rp, dict) and rp.get("contradiction"):
            out.append(str(rp["contradiction"]))
    return out


def compute_progress_board(tenant_id: str, plan: Any, dsn: str | None) -> ProgressBoard:
    """THE single on-demand board read path: resolve the active run from the 3 store seams
    (imported lazily from :mod:`studio.agui` to avoid an import cycle) and compute the
    board. ``build_progress_context`` and the run endpoint both go through here — one
    counting path, no mutable board table."""
    from studio.agui import _agent_runs_for, _tenant_actions, _tenant_runs

    runs = _tenant_runs(tenant_id, dsn)
    actions_all = _tenant_actions(tenant_id, dsn)
    run_id, record = resolve_active_run(runs, actions_all)
    if not run_id:
        return compute_board(None, None, [], [], plan)
    agent_runs = _agent_runs_for(run_id, dsn)
    run_actions = [a for a in actions_all if getattr(a, "run_id", None) == run_id]
    return compute_board(
        run_id, record, agent_runs, run_actions, plan,
        contradictions=_persisted_contradictions(agent_runs),
    )


def board_for_run(
    run_id: str, record: Any, agent_runs: list[dict[str, Any]], run_actions: list[Any],
    plan: Any,
) -> ProgressBoard:
    """Compute a board for a SPECIFIC run from already-read rows (the run endpoint path,
    which already loaded this run's agent_runs) — reuses the pure counting core."""
    return compute_board(
        run_id, record, agent_runs, run_actions, plan,
        contradictions=_persisted_contradictions(agent_runs),
    )


def replan_event_id(run_id: str, assumed: str, measured: str, sample_n: int) -> str:
    """A DETERMINISTIC agent_run id for the replan event, so recording it is exactly-once
    (the store's ON CONFLICT DO NOTHING dedupes a re-run with the same measurement)."""
    h = hashlib.sha256(f"{run_id}|{assumed}|{measured}|{sample_n}".encode()).hexdigest()
    return f"ar_replan_{h[:24]}"


def maybe_replan(
    blueprint: Any, agent_runs: list[dict[str, Any]], replans_so_far: int = 0
) -> PlanDelta | None:
    """The replan TRIGGER. Returns a concrete :class:`PlanDelta` ONLY when EVERY gate holds:

      * the blueprint records an assumed dominant objection,
      * ``replans_so_far < REPLAN_CAP`` (bounded — no thrash),
      * ``sample_n >= MIN_SAMPLE`` grounded-objection reads,
      * the measured winner beats the runner-up by ``>= MIN_MARGIN`` (a clear majority),
      * the measured winner ``!=`` the assumption.

    The returned delta is non-empty by construction (``from_objection != to_objection`` and
    it re-selects the measured objection's REAL offer + a new angle), so a decorative
    no-diff replan is impossible. Returns ``None`` otherwise."""
    assumed = (getattr(blueprint, "assumed_dominant_objection", None) or "").strip().lower()
    if not assumed or replans_so_far >= REPLAN_CAP:
        return None
    objections = _measured_objections(agent_runs)
    if len(objections) < MIN_SAMPLE:
        return None
    ranked = Counter(objections).most_common(2)
    measured, top_n = ranked[0]
    runner_up_n = ranked[1][1] if len(ranked) > 1 else 0
    if top_n - runner_up_n < MIN_MARGIN:
        return None
    if measured == assumed:
        return None
    new_offer_code = None
    try:
        from studio.campaign_blueprint import offer_rule_for

        rule = offer_rule_for(blueprint, measured)
        new_offer_code = rule.offer_code if rule else None
    except Exception:
        new_offer_code = None
    return PlanDelta(
        from_objection=assumed,
        to_objection=measured,
        new_offer_code=new_offer_code,
        new_angle=f"Lead with the '{measured}' objection the analyst measured across the cohort.",
        reason=(
            f"Blueprint assumed '{assumed}' dominates, but the analyst measured '{measured}' "
            f"in {top_n}/{len(objections)} grounded reads (margin {top_n - runner_up_n})."
        ),
    )
