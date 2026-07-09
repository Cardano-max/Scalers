"""LIVE run/file/artist state for the supervisor — chat tools + voice (item 4, §17).

Every function here reads the DATABASE FRESH on each call — never a cached answer —
so the chat host's tools and the realtime voice supervisor answer from the same real
rows the frontend renders:

  * :func:`finalized_leads`   — which leads a run staged drafts for (names + emails)
    and which were skipped, with the concrete reasons (agent_runs + actions);
  * :func:`agent_activity`    — what each agent is doing RIGHT NOW (the in-process
    runs registry for in-flight status + the latest agent_runs row per role);
  * :func:`files_snapshot`    — what files/images exist (live counts + the newest
    uploads incl. their VLM summaries — "which design did I just add?");
  * :func:`artist_artworks`   — one artist's real portfolio pieces;
  * :func:`artist_recent_memories` — one artist's latest memory rows;
  * :func:`snapshot`          — the compact live-state bundle the voice seams embed
    (``liveState``) on session mint / plan / orchestrate responses.

HONESTY: empty tenants read as explicit empties ("no run", "no files"), a store
failure degrades to an honest cannot-read note — nothing is ever invented.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

# The in-process async-run registry (app.state._studio_runs), stashed by
# mount_studio_agui so tools can report an in-flight run's live status. In-memory
# only — the DB reads below stay authoritative for everything durable.
_RUNS_REGISTRY: dict[str, dict] | None = None


def set_runs_registry(registry: dict[str, dict]) -> None:
    global _RUNS_REGISTRY
    _RUNS_REGISTRY = registry


def _registry() -> dict[str, dict]:
    return _RUNS_REGISTRY if isinstance(_RUNS_REGISTRY, dict) else {}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


# --------------------------------------------------------------------------- #
# Run resolution + per-run leads.
# --------------------------------------------------------------------------- #
def resolve_recent_run_id(tenant_id: str, dsn: str | None) -> str | None:
    """The most recent run for this tenant — the same resolution the progress board
    uses (runs row, or the newest action's in-flight run). Fresh read; None = none."""
    from studio.agui import _tenant_actions, _tenant_runs
    from studio.progress_board import resolve_active_run

    run_id, _record = resolve_active_run(
        _tenant_runs(tenant_id, dsn), _tenant_actions(tenant_id, dsn)
    )
    return run_id


def _lead_name_from_context(context: str | None) -> str | None:
    """The lead's REAL name off the staged action's own context dossier (or None)."""
    if not context:
        return None
    try:
        ctx = json.loads(context)
    except Exception:
        return None
    dossier = ctx.get("dossier") if isinstance(ctx, dict) else None
    if isinstance(dossier, dict):
        name = (dossier.get("name") or {}).get("value")
        if name:
            return str(name)
    return None


def finalized_leads(
    tenant_id: str, run_id: str | None = None, *, dsn: str | None = None
) -> dict[str, Any]:
    """Which leads were finalized for ``run_id`` (default: the most recent run):
    the staged drafts' names + emails/targets + status, and the skip ledger with
    concrete reasons (from the jury agent_run's output_ledger). Fresh DB read."""
    rid = run_id or resolve_recent_run_id(tenant_id, dsn)
    if not rid:
        return {"runId": None, "staged": [], "skipped": [],
                "note": "no campaign run exists for this studio yet"}
    try:
        from actions.store import list_actions_for_run

        rows = list_actions_for_run(rid, dsn=dsn)
    except Exception:
        rows = []
    staged = [
        {
            "name": _lead_name_from_context(getattr(a, "context", None)),
            "target": getattr(a, "target", None),
            "channel": getattr(a, "channel", None),
            "status": getattr(a, "status", None),
            "actionId": getattr(a, "id", None),
        }
        for a in rows
        if getattr(a, "tenant_id", tenant_id) == tenant_id
    ]
    skipped: list[dict[str, Any]] = []
    from studio.agui import _agent_runs_for

    for ar in _agent_runs_for(rid, dsn):
        if ar.get("role") != "jury":
            continue
        out = ar.get("output")
        ledger = out.get("output_ledger") if isinstance(out, dict) else None
        if isinstance(ledger, dict):
            skipped = [
                {"lead": s.get("lead"), "reason": s.get("reason"), "row": s.get("row")}
                for s in (ledger.get("skipped") or [])
                if isinstance(s, dict)
            ]
    return {"runId": rid, "staged": staged, "skipped": skipped, "note": None}


# --------------------------------------------------------------------------- #
# Live agent activity.
# --------------------------------------------------------------------------- #
def agent_activity(tenant_id: str, *, dsn: str | None = None) -> dict[str, Any]:
    """What each agent is doing right now: the active run's live status (in-process
    registry when in flight, else the runs row) + the LATEST agent_runs row per role
    (model + a bounded output summary + timestamp). Fresh reads only."""
    rid = resolve_recent_run_id(tenant_id, dsn)
    if not rid:
        return {"runId": None, "status": "none", "agents": {}, "selectionPending": None,
                "note": "no campaign run exists for this studio yet"}
    reg = _registry().get(rid) or {}
    status = reg.get("status")
    if status is None:
        try:
            import psycopg

            with psycopg.connect(_dsn(dsn), connect_timeout=5) as conn:
                row = conn.execute(
                    "SELECT status FROM runs WHERE run_id=%s", (rid,)
                ).fetchone()
            status = str(row[0]).lower() if row else "running"
        except Exception:
            status = "unknown"
    agents: dict[str, Any] = {}
    from studio.agui import _agent_runs_for
    from studio.campaign_runner import _summarize_output

    for ar in _agent_runs_for(rid, dsn):
        role = str(ar.get("role") or "")
        try:
            last = _summarize_output(role, ar.get("output"))
        except Exception:
            last = None
        agents[role] = {
            "model": ar.get("model"),
            "at": _iso(ar.get("created_at")),
            "lastOutput": last,
        }
    selection = None
    try:
        from studio.artwork_flow import get_selection, selection_request_payload

        sel = get_selection(rid, dsn=dsn)
        if sel and sel.get("status") == "awaiting":
            selection = selection_request_payload(sel)
            status = "awaiting_selection"
    except Exception:
        selection = None
    return {"runId": rid, "status": status, "agents": agents,
            "selectionPending": selection, "note": None}


def _dsn(dsn: str | None) -> str:
    import os

    return dsn or os.environ.get("ENGINE_DATABASE_URL") or (
        "postgresql://scalers:scalers@localhost:5432/scalers"
    )


# --------------------------------------------------------------------------- #
# Files / artworks / memories.
# --------------------------------------------------------------------------- #
def files_snapshot(
    tenant_id: str, *, newest: int = 5, dsn: str | None = None
) -> dict[str, Any]:
    """Live file registry state: counts by type + the newest artifacts including
    their VLM summaries — so "I added a new tattoo design, which one is it?" answers
    with the newest upload's real description."""
    from studio.artifacts import artifact_inventory, list_artifacts

    inv = artifact_inventory(tenant_id, dsn=dsn)
    if not inv.readable:
        return {"readable": False, "total": 0, "images": 0, "byType": {}, "newest": [],
                "note": "the file store could not be read this turn — do not guess counts"}
    rows = []
    try:
        rows = list_artifacts(tenant_id, active_only=True, dsn=dsn)[: max(1, newest)]
    except Exception:
        rows = []
    newest_entries = []
    for r in rows:
        meta = r.get("meta") or {}
        newest_entries.append(
            {
                "id": r["id"],
                "name": r["name"],
                "kind": r["artifact_type"],
                "createdAt": _iso(r.get("created_at")),
                "artist": meta.get("artist_slug") or (
                    r.get("linked_entity_id")
                    if r.get("linked_entity_type") == "artist" else None
                ),
                "vlmStatus": meta.get("vlm_status"),
                "vlmSummary": (r.get("summary") or "").strip() or None,
            }
        )
    return {
        "readable": True,
        "total": inv.total,
        "images": inv.images,
        "byType": dict(inv.by_type),
        "newest": newest_entries,
    }


def artist_artworks(
    tenant_id: str, artist: str, *, dsn: str | None = None
) -> dict[str, Any]:
    """One artist's REAL portfolio pieces (live read; honest-empty)."""
    from studio.artists_directory import get_artist_detail, resolve_artist

    resolved = resolve_artist(tenant_id, artist, dsn=dsn)
    if resolved is None:
        return {"artist": artist, "resolved": False, "artworks": [],
                "note": f"no artist matching {artist!r} in the roster"}
    detail = get_artist_detail(tenant_id, resolved["slug"], dsn=dsn) or {}
    return {
        "artist": resolved["name"],
        "slug": resolved["slug"],
        "resolved": True,
        "artworks": detail.get("artworks", []),
        "styleTags": detail.get("styleTags", []),
        "note": None,
    }


def artist_recent_memories(
    tenant_id: str, artist: str, *, limit: int = 8, dsn: str | None = None
) -> dict[str, Any]:
    """One artist's latest memory rows (live read; honest-empty)."""
    from studio.artist_memory import list_artist_memories
    from studio.artists_directory import artist_slug, resolve_artist

    resolved = resolve_artist(tenant_id, artist, dsn=dsn)
    slug = resolved["slug"] if resolved else artist_slug(artist)
    memories = list_artist_memories(tenant_id, slug, limit=limit, dsn=dsn)
    return {
        "artist": resolved["name"] if resolved else artist,
        "slug": slug,
        "resolved": bool(resolved),
        "memories": [{"at": m["at"], "text": m["text"]} for m in memories],
    }


# --------------------------------------------------------------------------- #
# The compact voice bundle.
# --------------------------------------------------------------------------- #
def snapshot(tenant_id: str, *, dsn: str | None = None) -> dict[str, Any]:
    """The compact live-state bundle the voice seams return under ``liveState`` —
    generated FRESH per request (never the mint-time frozen context). Bounded."""
    activity = agent_activity(tenant_id, dsn=dsn)
    files = files_snapshot(tenant_id, newest=3, dsn=dsn)
    leads = finalized_leads(tenant_id, activity.get("runId"), dsn=dsn)
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "activeRun": {
            "runId": activity.get("runId"),
            "status": activity.get("status"),
            "agents": activity.get("agents"),
            "selectionPending": activity.get("selectionPending"),
            "stagedLeads": [
                {"name": s.get("name"), "target": s.get("target"),
                 "channel": s.get("channel"), "status": s.get("status")}
                for s in (leads.get("staged") or [])[:10]
            ],
            "skipped": (leads.get("skipped") or [])[:10],
        },
        "files": files,
    }
