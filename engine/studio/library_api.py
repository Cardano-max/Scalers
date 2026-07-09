"""Library READ API — artifacts + artists (engine-core item 2).

Additive endpoints the frontend binds (tenant from ``STUDIO_TENANT_ID``):

* ``GET  /studio/artifacts?kind=&artist=`` →
  ``{"artifacts": [{id, kind, name, createdAt, artist, vlmStatus, hasPreview}]}``
* ``GET  /studio/artifacts/{id}/raw``       → the stored bytes, correct content-type
  (404 when no bytes exist — never a placeholder).
* ``GET  /studio/artists``                  →
  ``{"artists": [{slug, name, studios, artworkCount, campaignCount, memoryCount}]}``
* ``GET  /studio/artists/{slug}``           → the artist's full REAL record
  (styleTags from real artwork VLM tags, [] if none; artworks / campaigns /
  memories — every field real, empty arrays when no data).
* ``POST /studio/artists/{slug}/memory`` ``{"text": ...}`` → ``{ok, memoryId}``.

HONESTY: every field is a live read (artists/artist_studios, context_artifacts,
assets, campaign_examples, memories) — empty tenants return empty arrays, never
invented entries. Mounted via :func:`studio.console_api.mount_console_api`.
"""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

router = APIRouter()


def _tenant() -> str:
    return os.environ.get("STUDIO_TENANT_ID", "demo")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


@router.get("/studio/artifacts")
def studio_artifacts(kind: str | None = None, artist: str | None = None) -> dict:
    """The tenant's active artifacts, newest first — optionally filtered by kind
    (artifact_type) and/or linked artist (slug or name)."""
    from studio.artifacts import list_artifacts
    from studio.artists_directory import artist_slug

    kind = (kind or "").strip() or None
    want_artist = artist_slug(artist) if (artist or "").strip() else None
    # include_content with a 1-char bound: we only need the cheap has_preview flag,
    # never the (potentially large) parsed content in a list response.
    rows = list_artifacts(
        _tenant(), active_only=True, artifact_type=kind, include_content=True, content_limit=1
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        meta = r.get("meta") or {}
        linked = r.get("linked_entity_id") if r.get("linked_entity_type") == "artist" else None
        slug = linked or (meta.get("artist_slug") or None)
        if want_artist is not None and slug != want_artist:
            continue
        out.append(
            {
                "id": r["id"],
                "kind": r["artifact_type"],
                "name": r["name"],
                "createdAt": _iso(r.get("created_at")),
                "artist": slug,
                "vlmStatus": meta.get("vlm_status"),
                # True iff GET .../raw or the inline preview can actually serve bytes.
                "hasPreview": bool(r.get("has_preview")) or bool(meta.get("storage_path")),
            }
        )
    return {"artifacts": out}


@router.get("/studio/artifacts/{artifact_id}/raw")
def studio_artifact_raw(artifact_id: str) -> Response:
    """The artifact's REAL stored bytes (disk-backed uploads; legacy rows fall back
    to their inline preview data-URI). 404 when no bytes exist."""
    import base64

    from studio.artifact_files import read_stored_bytes
    from studio.artifacts import get_artifact

    art = get_artifact(artifact_id)
    if art is None or art["tenant_id"] != _tenant() or not art.get("active", True):
        raise HTTPException(status_code=404, detail="no such artifact")
    meta = art.get("meta") or {}
    media_type = art.get("media_type") or meta.get("media_type")

    raw = read_stored_bytes(meta.get("storage_path"))
    if raw is None:
        preview = art.get("preview") or ""
        if preview.startswith("data:"):
            head, _, rest = preview.partition(",")
            try:
                raw = base64.b64decode(rest, validate=False) if rest else None
            except Exception:
                raw = None
            if media_type is None and head.startswith("data:") and ";" in head:
                media_type = head[len("data:"):head.index(";")] or None
    if not raw:
        raise HTTPException(status_code=404, detail="no stored bytes for this artifact")
    return Response(content=raw, media_type=media_type or "application/octet-stream")


@router.get("/studio/artists")
def studio_artists() -> dict:
    """The REAL roster with live counts (artworks / campaigns / memories)."""
    from studio.artists_directory import list_artists

    return {"artists": list_artists(_tenant())}


@router.get("/studio/artists/{slug}")
def studio_artist_detail(slug: str) -> dict:
    """One artist's full real record — 404 for a slug that matches nobody."""
    from studio.artists_directory import get_artist_detail

    detail = get_artist_detail(_tenant(), slug)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"no artist {slug!r}")
    return {"artist": detail}


@router.post("/studio/artists/{slug}/memory")
async def studio_artist_memory(slug: str, request: Request):  # noqa: ANN202
    """Append one operator note to the artist's memory. Body ``{"text": ...}``.
    400 on empty text; 404 for an unknown artist (a memory needs a real subject)."""
    from studio.artist_memory import write_artist_memory
    from studio.artists_directory import resolve_artist

    try:
        payload = json.loads(await request.body() or b"{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    text = (payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "memory text is empty"}, status_code=400)
    tenant_id = _tenant()
    artist = resolve_artist(tenant_id, slug)
    if artist is None:
        return JSONResponse(
            {"ok": False, "error": f"no artist {slug!r} for tenant {tenant_id!r}"},
            status_code=404,
        )
    try:
        memory_id = write_artist_memory(
            tenant_id, artist["slug"], text, metadata={"kind": "operator_note"}
        )
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status_code=500
        )
    return JSONResponse({"ok": True, "memoryId": memory_id})


def mount_library_api(app) -> None:
    """Attach the library read endpoints (idempotent)."""
    if getattr(app.state, "_studio_library_mounted", False):
        return
    app.state._studio_library_mounted = True
    app.include_router(router)
