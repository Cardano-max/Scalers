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


@router.post("/studio/artists")
async def studio_artist_create(request: Request):  # noqa: ANN202
    """Create (or idempotently match) a roster artist. Body::

        {"name": "Kaps", "studio": "Skin Design Tattoos", "instagram": "@kaps",
         "email": "", "phone": "", "persona": "black & grey realism specialist",
         "brandVoice": "warm, direct, no discounts"}

    Only ``name`` is required. Re-POSTing the same name matches the existing row
    (never a duplicate). ``brandVoice``/``persona`` notes are stored verbatim —
    the persona lands on the artists row, the brand-voice note becomes a real
    artist memory the drafting brief loads. Nothing is fabricated or sent."""
    import uuid as _uuid

    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Json

    from studio.artist_memory import write_artist_memory
    from studio.artists_directory import _dsn, artist_slug

    try:
        payload = json.loads(await request.body() or b"{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    name = (payload.get("name") or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "artist name is required"},
                            status_code=400)
    studio_name = (payload.get("studio") or "").strip()
    instagram = (payload.get("instagram") or "").strip().lstrip("@")
    email = (payload.get("email") or "").strip() or None
    phone = (payload.get("phone") or "").strip() or None
    persona = (payload.get("persona") or "").strip() or None
    brand_voice = (payload.get("brandVoice") or payload.get("brand_voice") or "").strip()
    tenant_id = _tenant()
    slug = artist_slug(name)

    with psycopg.connect(_dsn(None), autocommit=True, row_factory=dict_row) as conn:
        existing = conn.execute(
            "SELECT id, name FROM artists WHERE tenant_id = %s AND lower(name) = lower(%s) LIMIT 1",
            (tenant_id, name),
        ).fetchone()
        if existing is not None:
            artist_id, created = existing["id"], False
            if persona:  # backfill only — never clobber an existing persona
                conn.execute(
                    "UPDATE artists SET artist_persona = COALESCE(artist_persona, %s) WHERE id = %s",
                    (persona, artist_id),
                )
        else:
            artist_id, created = f"artist_{slug}_{_uuid.uuid4().hex[:6]}", True
            conn.execute(
                "INSERT INTO artists (id, tenant_id, name, email, phone, is_test, "
                "artist_persona, artist_style_tags) VALUES (%s,%s,%s,%s,%s,FALSE,%s,%s)",
                (artist_id, tenant_id, name, email, phone, persona,
                 Json([s.strip() for s in (payload.get("styleTags") or [])
                       if str(s or "").strip()])),
            )
        if studio_name:
            conn.execute(
                "INSERT INTO artist_studios (artist_id, studio_name) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                (artist_id, studio_name),
            )

    memory_id = None
    notes = []
    if instagram:
        notes.append(f"Instagram: @{instagram}")
    if brand_voice:
        notes.append(f"Brand voice: {brand_voice}")
    if notes:
        try:
            memory_id = write_artist_memory(
                tenant_id, slug, " | ".join(notes),
                metadata={"kind": "brand_voice" if brand_voice else "profile",
                          "instagram": instagram or None},
            )
        except Exception:
            pass  # profile note is best-effort; the roster row already exists
    return JSONResponse({"ok": True, "artistId": artist_id, "slug": slug,
                         "created": created, "memoryId": memory_id})


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
