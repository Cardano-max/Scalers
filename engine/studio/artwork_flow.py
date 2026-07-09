"""Artwork TOP-PICKS + mid-run selection pause (engine-core item 3, spec §9/10/22).

When a campaign wants artwork attached (the provided-leads email path with
``plan.attach_artwork``, and every Instagram run), the engine:

  1. ranks the REAL portfolio (:mod:`studio.artwork_select`) into the TOP ``k``
     candidate pieces, each with the grounded "why" — deterministic (iterated
     ``select_artwork``), never an invented piece;
  2. if the run has NO recorded choice yet, persists an ``awaiting`` row in
     ``artwork_selections`` (durable — survives restarts) and the run PAUSES with a
     ``selection_request`` before any drafting;
  3. ``POST /studio/campaign/{run_id}/select-artwork`` records the pick
     (``selected``) and re-invokes the executor, which finds the choice here and
     proceeds — the durable replay-skip prevents re-drafting;
  4. if NO artwork exists for the artist/library the run does NOT pause: it
     proceeds without artwork and the board carries the honest note.

HONESTY: options come only from real ``assets`` library rows; the why traces to
stored tags; an empty library yields an empty option list, never a fabricated one.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

# The honest board/step note when nothing matched (spec: never pause on an empty
# library).
NO_ARTWORK_NOTE = "no matching artwork in the library — upload one to attach"

_SELECTIONS_SQL = (
    Path(__file__).resolve().parents[2] / "infra" / "initdb" / "23-artwork-selections.sql"
)


def _dsn(dsn: str | None = None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def _connect(dsn: str | None = None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), autocommit=True, row_factory=dict_row)


def ensure_schema(dsn: str | None = None) -> None:
    """Apply ``23-artwork-selections.sql`` (idempotent)."""
    with _connect(dsn) as conn:
        conn.execute(_SELECTIONS_SQL.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Top-k candidate ranking (pure over real library rows).
# --------------------------------------------------------------------------- #
def _asset_extras(tenant_id: str, dsn: str | None = None) -> dict[str, dict[str, Any]]:
    """asset_id -> the upload-only content fields (artifact_id / vlm_summary) the
    ArtworkRef model does not carry. Honest-empty on a store failure."""
    try:
        from team.store import TeamStore

        from studio.artwork_select import _portfolio_campaign_id

        rows = TeamStore(_dsn(dsn)).list_assets(_portfolio_campaign_id(tenant_id))
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        c = row.get("content") or {}
        if isinstance(c, dict):
            out[str(row.get("id") or "")] = {
                "artifact_id": c.get("artifact_id"),
                "vlm_summary": str(c.get("vlm_summary") or "") or None,
            }
    return out


def top_artwork_options(
    tenant_id: str,
    *,
    artist: str | None = None,
    theme_terms: list[str] | None = None,
    k: int = 4,
    dsn: str | None = None,
) -> list[dict[str, Any]]:
    """The TOP ``k`` candidate pieces for this artist + theme, each with the grounded
    why: ``[{assetId, artifactId, styles, motifs, why}]``. Deterministic — repeated
    ``select_artwork`` over the shrinking library, so rank 1 is exactly what the
    auto-pick would have chosen. ``[]`` when the artist/library has nothing (the
    caller proceeds without artwork; it never invents a piece)."""
    from studio.artwork_select import artist_styles, list_artwork, select_artwork

    pool = list_artwork(tenant_id, artist, dsn=dsn)
    if not pool:
        return []
    styles = artist_styles(pool)
    extras = _asset_extras(tenant_id, dsn)
    options: list[dict[str, Any]] = []
    remaining = list(pool)
    for _ in range(max(1, k)):
        if not remaining:
            break
        pick = select_artwork(remaining, artist_styles=styles, theme_terms=theme_terms)
        if pick is None:
            break
        extra = extras.get(pick.asset_id, {})
        options.append(
            {
                "assetId": pick.asset_id,
                "artifactId": extra.get("artifact_id"),
                "styles": list(pick.styles),
                "motifs": list(pick.motifs),
                "why": pick.why,
            }
        )
        remaining = [a for a in remaining if a.asset_id != pick.asset_id]
    return options


def resolve_pick(
    tenant_id: str, asset_id: str, *, dsn: str | None = None
) -> dict[str, Any] | None:
    """The selected piece's REAL context payload for staged actions:
    ``{assetId, artifactId, vlmSummary, artist, styles, motifs, caption}`` — or
    ``None`` when the asset does not exist in this tenant's library."""
    try:
        from team.store import TeamStore

        from studio.artwork_select import ARTWORK_ASSET_TYPE, _portfolio_campaign_id

        rows = TeamStore(_dsn(dsn)).list_assets(_portfolio_campaign_id(tenant_id))
    except Exception:
        return None
    for row in rows:
        if str(row.get("id") or "") != asset_id:
            continue
        if (row.get("asset_type") or "") != ARTWORK_ASSET_TYPE:
            continue
        c = row.get("content") or {}
        if not isinstance(c, dict):
            return None
        return {
            "assetId": asset_id,
            "artifactId": c.get("artifact_id"),
            "vlmSummary": str(c.get("vlm_summary") or "") or None,
            "artist": str(c.get("artist") or "") or None,
            "styles": [s for s in (c.get("styles") or []) if isinstance(s, str)],
            "motifs": [m for m in (c.get("motifs") or []) if isinstance(m, str)],
            "caption": str(c.get("caption") or "") or None,
        }
    return None


# --------------------------------------------------------------------------- #
# Durable selection state.
# --------------------------------------------------------------------------- #
def get_selection(run_id: str, *, dsn: str | None = None) -> dict[str, Any] | None:
    """The run's selection row (dict) or ``None``. Best-effort: an unreadable store
    reads as no selection (the run then proceeds as if un-gated — honest fallback)."""
    try:
        ensure_schema(dsn)
        with _connect(dsn) as conn:
            row = conn.execute(
                "SELECT run_id, tenant_id, session_id, status, question, options, "
                "plan, asset_id, artifact_id FROM artwork_selections WHERE run_id=%s",
                (run_id,),
            ).fetchone()
    except Exception:
        return None
    return dict(row) if row else None


def request_selection(
    run_id: str,
    tenant_id: str,
    session_id: str | None,
    *,
    question: str,
    options: list[dict[str, Any]],
    plan_snapshot: dict[str, Any] | None = None,
    dsn: str | None = None,
) -> None:
    """Persist the ``awaiting`` selection row (idempotent upsert; a re-entrant pause
    refreshes the options, a ``selected`` row is left untouched)."""
    from psycopg.types.json import Json

    ensure_schema(dsn)
    with _connect(dsn) as conn:
        conn.execute(
            """
            INSERT INTO artwork_selections
                (run_id, tenant_id, session_id, status, question, options, plan)
            VALUES (%s,%s,%s,'awaiting',%s,%s,%s)
            ON CONFLICT (run_id) DO UPDATE SET
                question = EXCLUDED.question,
                options = EXCLUDED.options,
                plan = COALESCE(EXCLUDED.plan, artwork_selections.plan),
                updated_at = now()
            WHERE artwork_selections.status = 'awaiting'
            """,
            (run_id, tenant_id, session_id, question, Json(options),
             Json(plan_snapshot) if plan_snapshot is not None else None),
        )


def record_choice(
    run_id: str, asset_id: str, *, artifact_id: str | None = None, dsn: str | None = None
) -> bool:
    """Record the operator's pick (awaiting → selected). Returns False when the run
    has no awaiting selection (already selected, or never paused)."""
    ensure_schema(dsn)
    with _connect(dsn) as conn:
        row = conn.execute(
            "UPDATE artwork_selections SET status='selected', asset_id=%s, "
            "artifact_id=%s, updated_at=now() "
            "WHERE run_id=%s AND status='awaiting' RETURNING run_id",
            (asset_id, artifact_id, run_id),
        ).fetchone()
    return row is not None


def selection_request_payload(row: dict[str, Any]) -> dict[str, Any]:
    """The ``selection_request`` shape the run state exposes (spec item 3):
    ``{"kind": "artwork", "question": ..., "options": [...]}``."""
    return {
        "kind": "artwork",
        "question": row.get("question") or "",
        "options": list(row.get("options") or []),
    }


# --------------------------------------------------------------------------- #
# The gate the executors call.
# --------------------------------------------------------------------------- #
def artwork_gate(
    run_id: str,
    tenant_id: str,
    session_id: str | None,
    plan: Any,
    *,
    artist: str | None,
    theme_terms: list[str] | None,
    dsn: str | None = None,
) -> tuple[str, Any]:
    """Decide the artwork step for this run. Returns one of:

    * ``("selected", pick_ctx)`` — a durable choice exists; attach ``pick_ctx``
      (``{assetId, artifactId, vlmSummary, ...}``) to every staged action;
    * ``("pause", selection_request)`` — top options exist and no choice was made:
      the caller STOPS before drafting and surfaces the request;
    * ``("none", note)`` — no artwork in the library: proceed WITHOUT artwork and
      record the honest note (never a pause the operator can't answer).
    """
    sel = get_selection(run_id, dsn=dsn)
    if sel and sel.get("status") == "selected" and sel.get("asset_id"):
        pick = resolve_pick(tenant_id, str(sel["asset_id"]), dsn=dsn)
        if pick is not None:
            return "selected", pick
        return "none", (
            f"the selected artwork {sel['asset_id']!r} is no longer in the library — "
            "proceeding without artwork"
        )
    if sel and sel.get("status") == "awaiting":
        return "pause", selection_request_payload(sel)

    options = top_artwork_options(
        tenant_id, artist=artist, theme_terms=theme_terms, dsn=dsn
    )
    if not options:
        return "none", NO_ARTWORK_NOTE
    question = (
        f"I found {len(options)} matching piece{'s' if len(options) != 1 else ''} "
        "for this campaign — which should I use?"
    )
    plan_snapshot = None
    try:
        plan_snapshot = plan.model_dump() if hasattr(plan, "model_dump") else None
    except Exception:
        plan_snapshot = None
    try:
        request_selection(
            run_id, tenant_id, session_id,
            question=question, options=options, plan_snapshot=plan_snapshot, dsn=dsn,
        )
    except Exception:
        # A store failure must not wedge the run behind a pause nobody can answer:
        # proceed without artwork, honestly noted.
        return "none", (
            "artwork options could not be persisted for selection — proceeding "
            "without artwork"
        )
    return "pause", {"kind": "artwork", "question": question, "options": options}


def theme_terms_from_plan(plan: Any, extra: list[str] | None = None) -> list[str]:
    """Bounded matching terms for artwork selection, drawn from the plan's REAL
    fields (campaign type / goal / offer type / segment) + any extras (e.g. the
    strategist's angle words). Pure."""
    terms: list[str] = []
    for attr in ("campaign_type", "offer_type", "segment", "goal", "audience"):
        v = getattr(plan, attr, "") or ""
        if isinstance(v, str) and v.strip():
            terms.extend(v.split())
    for e in extra or []:
        if isinstance(e, str) and e.strip():
            terms.extend(e.split())
    # De-dupe, keep order, bound the list.
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        key = t.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(t.strip())
        if len(out) >= 16:
            break
    return out


def awaiting_selection_summary(
    run_id: str | None,
    campaign_id: str | None,
    selection_request: dict[str, Any],
    *,
    channel: str,
    agent_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The run summary returned when the executor PAUSES for an artwork pick — the
    registry marks the run ``awaiting_selection`` and the poller surfaces the
    request. Nothing was drafted, staged, or sent."""
    n = len(selection_request.get("options") or [])
    return {
        "run_id": run_id,
        "campaign_id": campaign_id,
        "routed_channel": channel,
        "pipeline_built": True,
        "run_status": "awaiting_selection",
        "archetype_id": None,
        "agent_runs": list(agent_runs or []),
        "n_pending": 0,
        "n_queued": 0,
        "channels": [channel],
        "runs_row": False,
        "selection_request": selection_request,
        "message": selection_request.get("question")
        or f"{n} artwork option(s) await your pick before drafting.",
        "step_notes": [
            f"paused before drafting: {n} artwork option(s) surfaced for the "
            "operator's choice (POST /studio/campaign/{run_id}/select-artwork resumes)"
        ],
        "failure_summary": [],
    }
