"""Bridge the AG-UI Campaign Studio to the WIRED, traced Phase-A campaign spine.

The Studio Host owns a ``run_campaign`` tool (see :mod:`studio.agui`). When the
operator asks to run/launch/execute a campaign, that tool calls
:func:`run_and_trace` here, which:

1. CLASSIFIES the plan/brief to a registered archetype (real Haiku classifier;
   honest default ``artist_spotlight`` if classification is unavailable), and
2. runs the REAL Phase-A spine via :func:`archetypes.compose.run_campaign`
   (plan -> [research] -> strategy -> draft x N (capped Send) -> critique ->
   route pinned to HOLD -> queue). That writes per-role ``agent_runs`` + queued
   ``assets`` + ``asset_critiques`` + PENDING ``actions``. NOTHING is ever sent.
3. MATERIALIZES a ``runs`` row whose ``steps`` JSONB is the per-role agent_runs as
   top-level spans (node/model/input/output), so the per-step traces render in the
   EXISTING Runs UI + GraphQL ``runs`` query — the operator can literally watch
   what each agent thought.

Honesty: every model call is a real cell call inside ``compose``; this module adds
no content. Research degrades honest-empty without a provider key. The runs-row
materialization is best-effort and swallowed on failure — it never breaks the real
run and never fabricates a step. The only terminal effect is HELD/PENDING rows.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

# Default when the classifier is unavailable or returns an unknown id. Honest and
# explicit — not a silent guess; the summary reports which archetype actually ran.
_DEFAULT_ARCHETYPE = "artist_spotlight"

# Cap how much of an agent's input/output we mirror into a span (matches the
# harness span truncation budget so one row can't bloat).
_MAX_IO = 2000


def pick_archetype(brief: str) -> str:
    """Classify ``brief`` to a registered archetype id (real model call).

    Falls back to :data:`_DEFAULT_ARCHETYPE` on any failure or an unknown id, so a
    transient classifier error never blocks the run."""
    from archetypes import registry

    try:
        from archetypes.classify import classify_brief

        choice = classify_brief(brief)
        aid = getattr(choice, "archetype_id", None) or getattr(choice, "archetype", None)
        aid = aid.value if hasattr(aid, "value") else aid
        if isinstance(aid, str) and aid in registry.REGISTRY:
            return aid
    except Exception:
        pass
    return _DEFAULT_ARCHETYPE if _DEFAULT_ARCHETYPE in registry.REGISTRY else registry.ids()[0]


def _summarize_output(role: str, output: Any) -> str:
    """A short, readable one/two-liner for one role's output (for the chat trace)."""
    if not isinstance(output, dict):
        return str(output)[:240]
    if role == "researcher":
        return f"cited {output.get('cited', 0)} source(s); persisted {output.get('persisted', 0)}"
    if role == "strategist":
        ang = output.get("primary_angle") or output.get("angle") or output.get("big_idea") or ""
        conv = output.get("primary_conversion") or output.get("objective") or ""
        bits = [b for b in (ang, conv) if b]
        return " | ".join(str(b) for b in bits)[:240] or json.dumps(output)[:240]
    if role == "draft":
        hook = output.get("hook") or output.get("headline") or ""
        cap = output.get("caption") or ""
        cta = output.get("call_to_action") or output.get("cta") or ""
        return f"hook: {hook} · CTA: {cta}"[:240] or str(cap)[:240]
    if role == "critic":
        return f"verdict={output.get('verdict')} ({output.get('confidence')}) — {output.get('rationale','')[:160]}"
    if role == "jury":
        return f"aggregate={output.get('aggregate')}; decision={output.get('decision')}"
    return json.dumps(output)[:240]


def _materialize_runs_row(
    *, dsn: str | None, run_id: str, tenant_id: str, agent_runs: list[dict[str, Any]]
) -> bool:
    """Write a ``runs`` row whose ``steps`` are the per-role agent_runs as top-level
    spans, so the existing Runs query/UI surfaces node/model/input/output traces.

    Best-effort: returns True on success, False (swallowed) on any failure — a
    runs-row problem must never break the real campaign run."""
    try:
        from harness.runstore import PostgresRunStore, RunStatus
        from harness.spans import Span

        store = PostgresRunStore(dsn) if dsn else None
        if store is None:
            return False
        store.setup()
        store.start_run(run_id, tenant_id, "campaign", "studio")

        def _io(v: Any) -> str | None:
            return None if v is None else json.dumps(v)[:_MAX_IO]

        spans: list[Span] = []
        for i, ar in enumerate(agent_runs):
            now = datetime.now(timezone.utc).isoformat()
            role = str(ar.get("role") or "node")
            out = ar.get("output")
            spans.append(
                Span(
                    span_id=f"sp_{uuid.uuid4().hex[:16]}",
                    run_id=run_id,
                    node=role,
                    kind="node",
                    parent_span_id=None,
                    start_ts=now,
                    end_ts=now,
                    duration_ms=None,
                    input=_io(ar.get("input")),
                    output=_io(out),
                    model=ar.get("model"),
                    status="ok",
                    seq=i,
                    at=now,
                    text=f"{role}: {_summarize_output(role, out)}"[:240],
                    state=role,
                )
            )
        store.append_spans(run_id, spans)
        store.finish_run(run_id, status=RunStatus.COMPLETED, review_count=len(agent_runs))

        # Best-effort Langfuse mirror so the studio campaign run emits a trace with
        # one span per agent (strategist/draft/critic/jury) WHEN keys are present.
        # The studio path writes the runs row directly (it does not go through the
        # harness `execute_and_record` helper that mirrors), so without this call a
        # configured Langfuse would receive nothing for studio runs. ``mirror_run``
        # never raises and no-ops cleanly when unconfigured — it never gates the run.
        try:
            from observability import mirror_run

            mirror_run(run_id, tenant_id, spans, run_type="campaign")
        except Exception:
            pass
        return True
    except Exception:
        return False


def run_and_trace(
    *, brief: str, tenant_id: str, dsn: str | None = None, archetype_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run the real, traced Phase-A campaign for ``brief`` and return a structured
    summary (NOTHING sends; all outputs are HELD/PENDING).

    Returns a dict with: ``run_id``, ``campaign_id``, ``archetype_id``,
    ``agent_runs`` (list of {role, model, output_summary}), ``n_pending``,
    ``n_queued``, ``channels`` (from the spec), ``step_notes``, and
    ``runs_row`` (bool: whether the Runs-UI trace row was materialized).

    ``run_id`` may be supplied so the async studio run endpoint knows the id up front
    and can poll ``agent_runs`` live as each role lands.
    """
    from archetypes import registry
    from archetypes.compose import run_campaign as _compose_run

    aid = archetype_id or pick_archetype(brief)
    if aid not in registry.REGISTRY:
        aid = pick_archetype(brief)

    state = _compose_run(
        archetype_id=aid, tenant_id=tenant_id, brief=brief, dsn=dsn, persist=True, run_id=run_id
    )

    # Read back the per-role traces the spine just wrote (authoritative source).
    agent_runs: list[dict[str, Any]] = []
    try:
        from team.store import TeamStore

        ts = TeamStore(dsn) if dsn else None
        if ts is not None:
            ts.setup()
            for ar in ts.list_agent_runs(state.run_id):
                agent_runs.append(
                    {
                        "role": ar.get("role"),
                        "model": ar.get("model"),
                        "input": ar.get("input"),
                        "output": ar.get("output"),
                        "output_summary": _summarize_output(str(ar.get("role")), ar.get("output")),
                    }
                )
    except Exception:
        agent_runs = []

    runs_row = _materialize_runs_row(
        dsn=dsn, run_id=state.run_id, tenant_id=tenant_id, agent_runs=agent_runs
    )

    spec = registry.get(aid)
    channels = [c.value for c in spec.channels[: spec.fanout_cap]]

    return {
        "run_id": state.run_id,
        "campaign_id": state.campaign_id,
        "archetype_id": aid,
        "agent_runs": agent_runs,
        "n_pending": len(state.pending_action_ids),
        "n_queued": len(state.queued_asset_ids),
        "channels": channels,
        "step_notes": list(state.step_log),
        "runs_row": runs_row,
    }
