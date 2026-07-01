"""The durable, structured run-state board (P1.5 blueprint #3) + progress-aware replan.

``build_progress_context`` in :mod:`studio.agui` gives the host a TEXTUAL per-turn view
of a run. This module gives the same run a DURABLE, STRUCTURED board — the substrate the
UI renders and the limited-commitment replanning hook reads:

  * :class:`ProgressBoard` — known facts / missing unknowns / lead counts / objections
    resolved / contradictions / channels complete, computed from the SAME real stores
    (runs / agent_runs / actions) ``build_progress_context`` reads.
  * :func:`resolve_active_run` — the shared run-resolution logic (factored out of
    ``build_progress_context`` so both consume ONE implementation; agui passes the rows
    it already read, so the existing monkeypatchable seams stay the single read path).
  * :func:`compute_board` — pure counting over already-read rows (no DB, unit-testable).
  * :func:`snapshot` — read + resolve + compute for one run_id.
  * :func:`detect_contradiction` — the replanning trigger: a REAL, measured mismatch
    between the analyst's dominant objection and the blueprint's assumption. Returns a
    contradiction ONLY when the evidence genuinely contradicts the plan — never a
    decorative "replanned" note.

HONESTY: every field traces to a real row. No run → an honest empty board (all zero,
nothing invented). A store hiccup degrades to the empty board, never a fabricated count.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import BaseModel, Field


class ProgressBoard(BaseModel):
    """The structured state of one campaign run — the durable board the UI renders."""

    run_id: str | None = None
    run_status: str = "none"
    known: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    leads_total: int = 0
    leads_done: int = 0
    objections_resolved: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    channels_complete: list[str] = Field(default_factory=list)


def resolve_active_run(runs: list[Any], actions_all: list[Any]) -> tuple[str | None, Any]:
    """Resolve the ACTIVE run + its runs-row record from already-read rows.

    The latest ``runs`` row is authoritative, BUT a studio run materializes its runs row
    only at the END — so an in-flight run shows up in ``actions`` first. If the newest
    action points at a run with no runs row yet, surface THAT in-flight run instead (and
    return ``record=None`` to signal 'not materialized yet'). This is the exact logic
    ``build_progress_context`` used inline, factored out so both share one implementation."""
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
    """The REAL objection an analyst run measured (stated/inferred only). ``None`` when
    the signal was insufficient — never counted as a resolved objection."""
    if not isinstance(analyst_output, dict):
        return None
    signal = (analyst_output.get("objection_signal") or "").strip().lower()
    value = (analyst_output.get("primary_objection") or "").strip().lower()
    if signal in ("stated", "inferred") and value and value != "none-found":
        return value
    return None


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

    # leads_total: the intended fan-out (plan quota) when known, else the leads we
    # actually observed evidence for (researcher runs) — never a fabricated total.
    expected = int(
        (getattr(plan, "output_count", 0) or getattr(plan, "lead_count", 0) or 0)
    )
    leads_total = expected or len(researchers) or len(drafts)
    leads_done = len(drafts)

    objections = [o for o in (_objection_of(ar.get("output")) for ar in analysts) if o]

    # channels_complete: a channel whose staged-draft count meets its per-channel quota.
    per_channel_quota: dict[str, int] = {}
    try:
        from studio.campaign_blueprint import _distribute_quota, _default_channels

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
            f"{len(objections)} with a grounded objection."
        )
    if drafts:
        known.append(f"{len(drafts)} brand-voiced draft(s) staged HELD (approve-first).")
    n_pending = sum(1 for a in run_actions if getattr(a, "status", None) == "pending")
    if n_pending:
        known.append(f"{n_pending} draft(s) in the Review Queue awaiting approval.")

    missing: list[str] = []
    degraded = sum(
        1
        for ar in researchers
        if isinstance(ar.get("output"), dict) and ar["output"].get("degraded")
    )
    if degraded:
        missing.append(f"{degraded} lead(s) had no web research sources (degraded).")
    no_objection = sum(
        1 for ar in analysts if _objection_of(ar.get("output")) is None
    )
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
        objections_resolved=objections,
        contradictions=list(contradictions or []),
        channels_complete=channels_complete,
    )


def snapshot(run_id: str, tenant_id: str, dsn: str | None, plan: Any = None) -> ProgressBoard:
    """Compute the board for one run from the live stores.

    Reads through the SAME monkeypatchable seams ``build_progress_context`` uses (imported
    lazily from :mod:`studio.agui` to avoid an import cycle), so there is one read path.
    ``plan`` supplies the quota context (channels / output_count); ``None`` degrades to an
    empty-plan board (counts still real, quota-derived fields simply empty)."""
    from studio.agui import _agent_runs_for, _tenant_actions

    actions_all = _tenant_actions(tenant_id, dsn)
    agent_runs = _agent_runs_for(run_id, dsn)
    run_actions = [a for a in actions_all if getattr(a, "run_id", None) == run_id]
    # The runs record (for status) is best-effort; the board is honest without it.
    record = None
    try:
        from studio.agui import _tenant_runs

        record = next(
            (r for r in _tenant_runs(tenant_id, dsn) if getattr(r, "run_id", None) == run_id),
            None,
        )
    except Exception:
        record = None
    return compute_board(run_id, record, agent_runs, run_actions, plan)


def dominant_measured_objection(agent_runs: list[dict[str, Any]]) -> str | None:
    """The objection the analyst measured MOST across the run's leads (stated/inferred
    only), or ``None`` when no grounded objection was measured. Used by the replan hook to
    name the real, evidence-backed objection that displaced the blueprint's assumption."""
    objections = [
        o
        for o in (
            _objection_of(ar.get("output"))
            for ar in agent_runs
            if ar.get("role") == "analyst"
        )
        if o
    ]
    if not objections:
        return None
    return Counter(objections).most_common(1)[0][0]


def detect_contradiction(blueprint: Any, agent_runs: list[dict[str, Any]]) -> str | None:
    """The replanning TRIGGER (limited-commitment): compare the blueprint's assumed
    dominant objection against the objection the analyst actually measured MOST across the
    resolved leads. Return a human-readable contradiction ONLY when there is a real,
    measured mismatch backed by a majority of analyzed leads — else ``None`` (no
    decorative replan).

    Requires the assumption to exist AND at least 2 analyzed leads with a grounded
    objection AND a clear measured majority that differs from the assumption."""
    assumed = (getattr(blueprint, "assumed_dominant_objection", None) or "").strip().lower()
    if not assumed:
        return None
    objections = [
        o
        for o in (
            _objection_of(ar.get("output"))
            for ar in agent_runs
            if ar.get("role") == "analyst"
        )
        if o
    ]
    if len(objections) < 2:
        return None
    counts = Counter(objections)
    measured, measured_n = counts.most_common(1)[0]
    # A real contradiction: the measured majority differs from the assumption AND it is a
    # strict majority of the analyzed leads (so a single outlier can't trigger a replan).
    if measured != assumed and measured_n * 2 > len(objections):
        return (
            f"Blueprint assumed '{assumed}' dominates this cohort, but the analyst measured "
            f"'{measured}' across {measured_n}/{len(objections)} analyzed leads."
        )
    return None
