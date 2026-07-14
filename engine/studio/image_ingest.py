"""Image upload pipeline: disk bytes + VLM understanding + library/memory wiring.

This is the machinery behind ``POST /studio/upload/image`` (studio/agui.py). For one
uploaded image it:

  1. writes the REAL bytes to disk (``var/artifacts/{tenant}/{sha256}.{ext}`` —
     content-addressed, never truncated like the old 200k-char data-URI column);
  2. runs REAL VLM analysis via :mod:`studio.ingest_vlm` (Anthropic, tattoo-specific
     instruction) extracting style / motif / color-vs-black-and-grey / mood /
     complexity / campaign-fit — image-level facts, honestly labelled;
  3. registers the ``context_artifacts`` row (metadata + storage_path + a bounded
     <=64k thumbnail data-URI — an oversized image stores NO thumbnail rather than a
     truncated one) with the facts in ``parsed_content``;
  4. adds an ``assets`` LIBRARY row (artwork_source-style: ``source='upload'``,
     ``content.artist/styles/motifs/vlm_summary/artifact_id``) so
     :mod:`studio.artwork_select` can select the piece for campaigns; and
  5. records an ARTIST memory ("new design uploaded: <vlm one-liner>; operator
     prompt: <prompt>") when the upload names a real artist.

HONEST DEGRADATION: if the VLM is unconfigured or errors, the image + artist +
prompt are still stored and ``vlm_status`` says exactly why — tags are NEVER
fabricated, and the caller's summary states what was and was not captured.
"""

from __future__ import annotations

from typing import Any

# Thumbnail bound: a data-URI at or under this many characters is stored as the
# artifact preview; anything larger stores NO thumbnail (never a truncated one).
THUMBNAIL_MAX_CHARS = 65_536

# VLM fact field -> the structured bucket it lands in.
_FIELD_BUCKETS: dict[str, str] = {
    "style": "styles",
    "visual_style": "styles",
    "motif": "motifs",
    "subject": "motifs",
    "color": "color_mode",
    "mood": "mood",
    "vibe": "mood",
    "complexity": "complexity",
    "campaign_fit": "campaign_fit",
}


def _bucket_facts(facts: list[Any]) -> dict[str, Any]:
    """Project the VLM's tagged facts into the structured artwork fields. Pure —
    every value is a real extracted fact; unmapped tags stay in ``other``."""
    out: dict[str, Any] = {
        "styles": [], "motifs": [], "color_mode": "", "mood": "",
        "complexity": "", "campaign_fit": [], "other": [],
    }

    def _add_unique(key: str, value: str) -> None:
        vals = out[key]
        if value and value.lower() not in {v.lower() for v in vals}:
            vals.append(value)

    for f in facts:
        field = str(getattr(f, "field", "") or "").strip().lower()
        value = str(getattr(f, "value", "") or "").strip()
        if not value:
            continue
        bucket = _FIELD_BUCKETS.get(field)
        if bucket in ("styles", "motifs", "campaign_fit"):
            _add_unique(bucket, value)
        elif bucket in ("color_mode", "mood", "complexity"):
            if not out[bucket]:
                out[bucket] = value
        else:
            out["other"].append(f"{field}: {value}" if field else value)
    return out


def _one_liner(tags: dict[str, Any]) -> str:
    """A one-line summary COMPOSED ONLY of the real extracted values (no adjectives
    invented here). '' when nothing was extracted."""
    bits: list[str] = []
    if tags.get("styles"):
        bits.append("/".join(tags["styles"][:3]))
    if tags.get("motifs"):
        bits.append("motif: " + ", ".join(tags["motifs"][:3]))
    if tags.get("color_mode"):
        bits.append(tags["color_mode"])
    if tags.get("mood"):
        bits.append("mood: " + tags["mood"])
    if tags.get("complexity"):
        bits.append(tags["complexity"])
    return "; ".join(bits)


def _render_facts(facts: list[Any]) -> str:
    """The artifact ``parsed_content``: one honest line per extracted fact."""
    lines = []
    for f in facts:
        field = str(getattr(f, "field", "") or "fact")
        value = str(getattr(f, "value", "") or "")
        if value:
            lines.append(f"[{field}] {value}")
    return "\n".join(lines)


def analyze_image(
    tenant_id: str, name: str, raw: bytes, media_type: str | None
) -> dict[str, Any]:
    """Run the REAL VLM extraction over the image bytes. Returns
    ``{status, tags, facts_text, summary, model, fact_count, error}`` where status is
    ``ok`` | ``no_facts`` | ``unavailable``. Never fabricates: an unconfigured or
    failing model yields ``unavailable`` with the concrete reason. Monkeypatchable
    seam for tests."""
    from studio import ingest_vlm

    if not ingest_vlm.is_configured():
        return {
            "status": "unavailable",
            "tags": {}, "facts_text": "", "summary": "", "model": None,
            "fact_count": 0,
            "error": "no ANTHROPIC_API_KEY / anthropic SDK — visual analysis skipped",
        }
    try:
        result = ingest_vlm.ingest_bytes(
            tenant_id,
            name,
            raw,
            media_type=media_type or "image/png",
            instruction=ingest_vlm.TATTOO_IMAGE_INSTRUCTION,
        )
    except Exception as exc:  # honest failure — image kept, tags never invented
        return {
            "status": "unavailable",
            "tags": {}, "facts_text": "", "summary": "", "model": None,
            "fact_count": 0,
            "error": f"VLM analysis failed: {type(exc).__name__}: {exc}",
        }
    tags = _bucket_facts(result.facts)
    return {
        "status": "ok" if result.facts else "no_facts",
        "tags": tags,
        "facts_text": _render_facts(result.facts),
        "summary": _one_liner(tags),
        "model": result.model,
        "fact_count": len(result.facts),
        "error": None,
    }


def process_image_upload(
    tenant_id: str,
    name: str,
    raw: bytes,
    *,
    media_type: str | None = None,
    kind: str = "image",
    artist: str | None = None,
    prompt: str | None = None,
    dsn: str | None = None,
) -> dict[str, Any]:
    """The full upload pipeline (sync; the route offloads it). Returns the honest
    summary JSON the route responds with. See the module docstring for the steps."""
    import base64

    from studio.artifact_files import store_bytes
    from studio.artifacts import register_artifact
    from studio.artists_directory import artist_slug as _slugify
    from studio.artists_directory import resolve_artist

    # kind="competitor": the image is a COMPETITOR post screenshot — the VLM
    # researches the image itself and it is filed as a competitor_posts row for
    # creative-intelligence scoring. It must NEVER enter our artwork library or
    # artist memory (someone else's work is study material, not an asset).
    is_competitor = kind == "competitor"
    artifact_type = (
        "screenshot" if is_competitor
        else kind if kind in ("image", "artwork", "screenshot")
        else "image"
    )
    prompt = (prompt or "").strip()

    # 1) Bytes to disk — content-addressed, never truncated.
    sha, path = store_bytes(tenant_id, raw, media_type=media_type, name=name)

    # Thumbnail: keep the original as a data-URI when small enough; otherwise none.
    b64 = base64.standard_b64encode(raw).decode("ascii")
    data_uri = f"data:{media_type or 'image/png'};base64,{b64}"
    preview = data_uri if len(data_uri) <= THUMBNAIL_MAX_CHARS else None

    # Artist resolution against the REAL roster (honest miss recorded, never guessed).
    resolved = resolve_artist(tenant_id, artist, dsn=dsn) if artist else None
    artist_name = resolved["name"] if resolved else (artist or "").strip()
    slug = resolved["slug"] if resolved else (_slugify(artist) if artist else "")

    # 2) REAL VLM analysis (or an honest unavailable).
    vlm = analyze_image(tenant_id, name, raw, media_type)
    tags = vlm.get("tags") or {}

    # 3) Register the context artifact — deterministic on (tenant, content hash,
    #    name): re-uploading the same file refreshes its ONE row; the same bytes
    #    under a DIFFERENT name are a distinct artifact (both share the one disk
    #    blob — content-addressed storage).
    import hashlib as _hashlib

    _key = _hashlib.sha1(f"{tenant_id}|{sha}|{name}".encode("utf-8")).hexdigest()[:16]
    artifact_id = f"art_img_{_key}"
    meta: dict[str, Any] = {
        "bytes": len(raw),
        "sha256": sha,
        "storage_path": str(path),
        "media_type": media_type,
        "vlm_status": vlm["status"],
        "vlm_model": vlm.get("model"),
        "artist": artist_name or None,
        "artist_slug": slug or None,
        "artist_resolved": bool(resolved),
        "operator_prompt": prompt or None,
    }
    if vlm.get("error"):
        meta["vlm_error"] = vlm["error"]
    if vlm["status"] == "ok":
        meta["vlm"] = {
            "styles": tags.get("styles", []),
            "motifs": tags.get("motifs", []),
            "color_mode": tags.get("color_mode", ""),
            "mood": tags.get("mood", ""),
            "complexity": tags.get("complexity", ""),
            "campaign_fit": tags.get("campaign_fit", []),
        }
    if preview is None:
        meta["preview_omitted"] = (
            f"image data-URI {len(data_uri)} chars exceeds the {THUMBNAIL_MAX_CHARS} "
            "thumbnail cap — full bytes on disk, no truncated thumbnail stored"
        )
    summary_bits = [f"{(media_type or 'image').split('/')[-1].upper()} image, {len(raw):,} bytes"]
    if vlm.get("summary"):
        summary_bits.append(vlm["summary"])
    register_artifact(
        tenant_id,
        name,
        artifact_type,
        media_type=media_type,
        summary=" — ".join(summary_bits),
        parsed_content=vlm.get("facts_text") or "",
        preview=preview,
        source="upload",
        linked_entity_type="artist" if slug else None,
        linked_entity_id=slug or None,
        meta=meta,
        artifact_id=artifact_id,
        dsn=dsn,
    )

    # 4') Competitor screenshot → a REAL competitor_posts row with VLM-derived
    #     visual_tags (the image is the research object). Kept OUT of the artwork
    #     library and artist memory below.
    competitor_post: dict[str, Any] | None = None
    competitor_error: str | None = None
    if is_competitor:
        try:
            from studio.competitor_intel import record_screenshot_post

            competitor_post = record_screenshot_post(
                tenant_id, name=name, prompt=prompt, vlm=vlm,
                artifact_id=artifact_id, sha=sha, dsn=dsn,
            )
        except Exception as exc:  # honest: report, never claim the row exists
            competitor_error = f"{type(exc).__name__}: {exc}"

    # 4) Library row so artwork_select can pick this piece for campaigns.
    asset_id: str | None = None
    asset_error: str | None = None
    if artifact_type in ("image", "artwork") and not is_competitor:
        try:
            from team.store import TeamStore

            from studio.artwork_select import (
                ARTWORK_ASSET_TYPE,
                ARTWORK_STATUS,
                _portfolio_campaign_id,
            )

            store = TeamStore(_resolve_dsn(dsn))
            store.setup()
            asset_id = f"art_upload_{tenant_id}_{_key}"
            store.record_asset(
                id=asset_id,
                campaign_id=_portfolio_campaign_id(tenant_id),
                asset_type=ARTWORK_ASSET_TYPE,
                content={
                    "artist": artist_name,
                    "image_ref": f"artifact://{artifact_id}",
                    "caption": (prompt or name or "").strip(),
                    "styles": tags.get("styles", []),
                    "motifs": tags.get("motifs", []),
                    "collection": "",
                    "is_best_example": False,
                    "source": "upload",
                    "vlm_summary": vlm.get("summary") or "",
                    "vlm_status": vlm["status"],
                    "vlm_error": vlm.get("error") or "",
                    "color_mode": tags.get("color_mode", ""),
                    "mood": tags.get("mood", ""),
                    "complexity": tags.get("complexity", ""),
                    "campaign_fit": tags.get("campaign_fit", []),
                    "artifact_id": artifact_id,
                },
                status=ARTWORK_STATUS,
            )
        except Exception as exc:  # honest: report, never claim a library row exists
            asset_id = None
            asset_error = f"{type(exc).__name__}: {exc}"

    # 5) Artist memory ("new design uploaded ...") — only when a real artist is
    #    named, and never for a competitor screenshot (not our artist's work).
    memory_id: str | None = None
    memory_error: str | None = None
    if slug and not is_competitor:
        try:
            from studio.artist_memory import write_artist_memory

            desc = vlm.get("summary") or "visual analysis unavailable"
            text = f"New design uploaded: {desc}"
            if prompt:
                text += f"; operator prompt: {prompt}"
            memory_id = write_artist_memory(
                tenant_id,
                slug,
                text,
                metadata={
                    "kind": "artwork_upload",
                    "artifact_id": artifact_id,
                    "asset_id": asset_id,
                    "vlm_status": vlm["status"],
                },
                dsn=dsn,
            )
        except Exception as exc:
            memory_error = f"{type(exc).__name__}: {exc}"

    # The full honest summary.
    if is_competitor and vlm["status"] == "ok":
        note = (
            "Competitor screenshot analyzed for real (VLM over the image) and filed "
            "as a competitor post — its visual pattern now feeds creative-intelligence "
            "scoring and pattern molding. It was NOT added to your artwork library "
            "(never reused as your own work). Nothing was sent."
        )
    elif is_competitor:
        note = (
            "Competitor screenshot stored and filed as a competitor post, but visual "
            f"analysis was NOT captured ({vlm.get('error') or 'no facts extracted'}) — "
            "no visual tags were fabricated; caption/handle from your note only. "
            "It was NOT added to your artwork library. Nothing was sent."
        )
    elif vlm["status"] == "ok":
        note = (
            "Image stored on disk and analyzed for real (VLM extraction, image-level "
            "facts); it is in the artwork library and linked to the artist. Nothing "
            "was sent."
        )
    elif vlm["status"] == "no_facts":
        note = (
            "Image stored on disk; the VLM ran but extracted no facts (nothing was "
            "invented to fill the gap). The image is countable and selectable, "
            "untagged. Nothing was sent."
        )
    else:
        note = (
            "Image stored on disk with artist + prompt metadata. Visual analysis was "
            f"NOT captured ({vlm.get('error')}) — no tags were fabricated. "
            "Nothing was sent."
        )
    out: dict[str, Any] = {
        "ok": True,
        "id": artifact_id,
        "name": name,
        "type": artifact_type,
        "bytes": len(raw),
        "sha256": sha,
        "storagePath": str(path),
        "hasPreview": preview is not None,
        "artist": {
            "input": artist or None,
            "name": artist_name or None,
            "slug": slug or None,
            "resolved": bool(resolved),
        },
        "prompt": prompt or None,
        "vlmStatus": vlm["status"],
        "vlm": (
            {
                "styles": tags.get("styles", []),
                "motifs": tags.get("motifs", []),
                "colorMode": tags.get("color_mode", ""),
                "mood": tags.get("mood", ""),
                "complexity": tags.get("complexity", ""),
                "campaignFit": tags.get("campaign_fit", []),
                "summary": vlm.get("summary") or "",
                "factCount": vlm.get("fact_count", 0),
                "model": vlm.get("model"),
            }
            if vlm["status"] in ("ok", "no_facts")
            else None
        ),
        "vlmError": vlm.get("error"),
        # Top-level mirror of vlm.summary — the console ack reads THIS field to
        # show "Visual analysis: …" (nested-only summary rendered as a generic
        # "Uploaded." note, hiding a real analysis from the operator).
        "vlmSummary": (vlm.get("summary") or None)
        if vlm["status"] in ("ok", "no_facts") else None,
        "assetId": asset_id,
        "memoryId": memory_id,
        "note": note,
    }
    if is_competitor:
        out["competitorPost"] = competitor_post
        if competitor_error:
            out["competitorError"] = competitor_error
    if asset_error:
        out["assetError"] = asset_error
    if memory_error:
        out["memoryError"] = memory_error
    return out


def _resolve_dsn(dsn: str | None) -> str:
    import os

    return dsn or os.environ.get("ENGINE_DATABASE_URL") or (
        "postgresql://scalers:scalers@localhost:5432/scalers"
    )
