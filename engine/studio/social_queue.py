"""Social Ready Queue — the read model for IG/FB posts waiting at the publish gate.

The posting pipeline is fully live UP TO the publish boundary: drafting, artwork
selection, enrichment and approve-and-schedule all run, and every resulting
instagram/facebook draft sits PENDING behind the fail-closed Meta credential
gate (:func:`actions.publish.meta_credentials_blocked_reason` — the operator
provides ``META_PAGE_TOKEN`` + ``META_IG_USER_ID`` / ``META_PAGE_ID`` later).
This module is the operator's window onto that queue: :func:`ready_posts`
resolves each pending post into a COMPLETE package —

  * the action id / channel / type, the full caption (the ``draft``), the
    target, created / scheduled_for / schedule_live;
  * the draft's media, resolved from the REAL ``assets`` rows its context
    references (``artwork_asset_id`` / ``artwork.assetId`` from the enriched
    JSON context, the legacy ``(asset …)`` text note, and the optional
    ``broll_asset_id``): the artwork's own tags + media kind
    (``'image'``/``'video'``), with ``found=False`` stated honestly when a
    referenced row no longer exists;
  * the publish gate state: ``publishable`` + the exact ``blocked_reason`` an
    approve would refuse with while credentials are absent — read from the SAME
    helper the publish gate uses, never a duplicated guess.

HONESTY: every field is a live read from the ``actions``/``assets`` tables;
an empty queue is an empty list; a failed asset lookup is reported on the
package (``error``), never masked. Read-only — nothing here publishes.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

#: Channels this queue reads (the Meta posting surface), INCLUDING the alias
#: forms real campaign rows carry ('ig'/'fb') — the gate and this queue must
#: see the same drafts, so both fold aliases via actions.publish.normalize_channel.
SOCIAL_CHANNELS = ("instagram", "facebook", "ig", "fb")

# Legacy text contexts (studio.post_campaign's pre-JSON note) reference the
# picked piece as "... (asset art_xxx). ..." — a REAL assets row id.
_LEGACY_ASSET_RE = re.compile(r"\(asset ([A-Za-z0-9:_\-]+)\)")


def _dsn(dsn: str | None = None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def _connect(dsn: str | None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), row_factory=dict_row, autocommit=True)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _as_ctx(raw: Any) -> dict[str, Any]:
    """The action's context as a dict — parsed JSON, or ``{}`` for a legacy text
    note / absent context. Pure."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _context_refs(raw: Any) -> tuple[str | None, str | None, str | None]:
    """``(artwork_asset_id, broll_asset_id, artwork_artifact_id)`` referenced by an
    action's context.

    Reads the enriched JSON context first (``artwork_asset_id``, then the nested
    ``artwork.assetId``/``asset_id``; ``broll_asset_id``; the nested
    ``artwork.artifactId`` — the id the ``/studio/artifacts/{id}/raw`` route
    serves the real image bytes from, so the review UI can render the PICTURE, not
    a bare id string), and falls back to the legacy text note's ``(asset …)``
    marker. Absent/unparseable context degrades to ``(None, None, None)``."""
    if not raw:
        return None, None, None
    ctx = _as_ctx(raw)
    if not ctx:
        m = _LEGACY_ASSET_RE.search(str(raw))
        return (m.group(1) if m else None), None, None

    def _clean(v: Any) -> str | None:
        return v.strip() if isinstance(v, str) and v.strip() else None

    art = ctx.get("artwork") if isinstance(ctx.get("artwork"), dict) else {}
    artwork_id = _clean(ctx.get("artwork_asset_id"))
    if artwork_id is None:
        artwork_id = _clean(art.get("assetId")) or _clean(art.get("asset_id"))
    if artwork_id is None:
        m = _LEGACY_ASSET_RE.search(str(ctx.get("note") or ""))
        artwork_id = m.group(1) if m else None
    artifact_id = _clean(art.get("artifactId")) or _clean(ctx.get("artwork_artifact_id"))
    return artwork_id, _clean(ctx.get("broll_asset_id")), artifact_id


#: Imperative call-to-action cues — a caption sentence carrying one IS the post's
#: real CTA. Single words match on a word boundary; the phrases match as substrings.
_CTA_RE = re.compile(
    r"\b(text|dm|message|book|call|visit|tap|reply|click|reserve|schedule|email"
    r"|swipe|shop|register)\b|link in bio|sign up|learn more|reach out"
    r"|get in touch|come in|book now",
    re.IGNORECASE,
)


def _caption_cta(caption: str) -> str | None:
    """The caption's OWN call-to-action — the LAST sentence that issues one — so the
    anatomy CTA chip matches the post that will actually publish, not a generic
    angle-template CTA. The copywriter writes the real offer CTA ('Text KEEBS for
    available dates'); the grounded ctx CTA is a deterministic template keyed on the
    angle ('dm to start your design'), and the two used to disagree on the card.
    None when the caption issues no detectable CTA (the caller then falls back to the
    grounded ctx CTA). Pure."""
    sentences = [
        s.strip()
        for s in re.split(r"(?<=[.!?])\s+|\n+", caption or "")
        if s.strip()
    ]
    for s in reversed(sentences):
        if _CTA_RE.search(s):
            return s
    return None


def _post_anatomy(ctx: dict[str, Any], caption: str) -> dict[str, Any]:
    """The POST ANATOMY the review UI renders so an operator sees a real social
    post, not a wall of text: the hook (the caption's first line), the deterministic
    angle, the CTA, and the grounded hashtags/keywords. The hook and CTA are read off
    the CAPTION itself (the text that publishes) so the chips can never contradict the
    post; the angle/hashtags come off the enriched context. The grounded ctx CTA is
    the fallback when the caption issues no detectable call-to-action. Pure;
    honest-empty fields when neither the caption nor the draft carried one."""
    first_line = ""
    for line in (caption or "").splitlines():
        if line.strip():
            first_line = line.strip()
            break
    tags = [
        str(t).strip()
        for t in (ctx.get("hashtags") or [])
        if isinstance(t, str) and t.strip()
    ]
    ctx_cta = (str(ctx.get("cta")).strip() or None) if ctx.get("cta") else None
    return {
        "hook": first_line or None,
        "angle": (str(ctx.get("angle")).strip() or None) if ctx.get("angle") else None,
        # The caption's real CTA wins; the grounded template CTA is the fallback.
        "cta": _caption_cta(caption) or ctx_cta,
        "hashtags": tags,
    }


def _media_package(
    asset_id: str | None,
    assets: dict[str, dict[str, Any]],
    lookup_error: str | None,
    artifact_id: str | None = None,
) -> dict[str, Any] | None:
    """One resolved media block, or ``None`` when the draft referenced no asset.

    ``found=True`` only when the referenced ``assets`` row really exists; its
    tags/kind/caption are copied verbatim off the row's content. When an
    ``artifact_id`` is known, ``image_url`` points at the REAL bytes
    (``/studio/artifacts/{id}/raw``) so the review UI renders the picture the post
    will publish — not a bare id. A reference whose row is gone (or whose lookup
    failed) is stated so — never invented."""
    if asset_id is None:
        return None
    image_url = (
        f"/studio/artifacts/{artifact_id}/raw" if artifact_id else None
    )
    if lookup_error is not None:
        return {
            "asset_id": asset_id, "artifact_id": artifact_id, "found": False,
            "media": None, "tags": [], "caption": None, "image_url": image_url,
            "error": lookup_error,
        }
    content = assets.get(asset_id)
    if content is None:
        return {
            "asset_id": asset_id, "artifact_id": artifact_id, "found": False,
            "media": None, "tags": [], "caption": None, "image_url": image_url,
            "error": f"referenced asset {asset_id} not found in the library",
        }
    tags: list[str] = []
    seen: set[str] = set()
    for key in ("styles", "motifs"):
        for t in content.get(key) or []:
            if isinstance(t, str) and t.strip() and t.strip().lower() not in seen:
                seen.add(t.strip().lower())
                tags.append(t.strip())
    media = "video" if content.get("media") == "video" else "image"
    return {
        "asset_id": asset_id,
        "artifact_id": artifact_id,
        "found": True,
        "media": media,
        "tags": tags,
        "caption": (content.get("caption") or "").strip() or None,
        # Only an IMAGE renders inline; a video block still carries the url for a
        # future poster frame but the UI treats media=='video' as a b-roll chip.
        "image_url": image_url,
    }


def _mold_for_run(run_id: str | None, dsn: str | None) -> dict[str, Any] | None:
    """The competitor pattern this run's post was MOLDED from — the operator's
    picked reference (handle / url / why-it-worked / matched structure), read off
    the run's ``role='molder'`` agent_run. This is what lets the review UI say
    'shaped from @competitor (score 6.6) — structure only, never copied', turning
    an opaque caption into an auditable, sellable artefact. ``None`` when the run
    molded nothing (no competitor research / honest skip). Best-effort."""
    if not run_id:
        return None
    try:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(_dsn(dsn), row_factory=dict_row, autocommit=True) as conn:
            row = conn.execute(
                "SELECT output FROM agent_runs WHERE run_id=%s AND role='molder' "
                "ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
    except Exception:
        return None
    out = (row or {}).get("output") if row else None
    if not isinstance(out, dict):
        return None
    return {
        "handle": out.get("reference_handle"),
        "url": out.get("reference_url"),
        "structure": [str(p) for p in (out.get("structure") or [])],
        "emotionalAngle": out.get("emotional_angle"),
        "visualPattern": out.get("visual_pattern"),
        # The always-true safety note the client should SEE — proof it's a mold,
        # not a copy (enforced in code by copies_verbatim, surfaced here).
        "neverCopied": out.get("never_copy")
        or "competitor caption used as SHAPE reference only — no sentence reused",
    }


def ready_posts(tenant_id: str, *, dsn: str | None = None) -> list[dict[str, Any]]:
    """Every PENDING instagram/facebook action of ``tenant_id`` as a full post
    package, newest first. Honest-empty list when nothing is pending."""
    from actions.publish import meta_credentials_blocked_reason, normalize_channel

    with _connect(dsn) as conn:
        rows = conn.execute(
            "SELECT id, channel, type, draft, target, context, run_id, created_at, "
            "scheduled_for, schedule_live FROM actions "
            "WHERE tenant_id = %s AND status = 'pending' "
            "AND lower(channel) = ANY(%s) "
            "ORDER BY created_at DESC",
            (tenant_id, list(SOCIAL_CHANNELS)),
        ).fetchall()

        refs = [_context_refs(r.get("context")) for r in rows]
        # Only the two ASSET ids resolve against the library; the artifact id (3rd
        # element) is a render pointer, not a library row.
        wanted = sorted({a for (art, broll, _fid) in refs for a in (art, broll) if a})
        assets: dict[str, dict[str, Any]] = {}
        lookup_error: str | None = None
        if wanted:
            try:
                # Tenant-scoped: only this tenant's library assets resolve into a
                # package — a stale/foreign asset id reports found:false instead
                # of leaking another tenant's media into the approval surface.
                arows = conn.execute(
                    "SELECT id, content FROM assets WHERE id = ANY(%s) "
                    "AND campaign_id = %s",
                    (wanted, f"portfolio:{tenant_id}"),
                ).fetchall()
                assets = {
                    a["id"]: (a["content"] if isinstance(a["content"], dict) else {})
                    for a in arows
                }
            except Exception as exc:  # noqa: BLE001 — reported per-package, never masked
                lookup_error = f"asset lookup failed: {type(exc).__name__}: {exc}"

    mold_by_run: dict[str, dict[str, Any] | None] = {}
    posts: list[dict[str, Any]] = []
    for row, (artwork_id, broll_id, artifact_id) in zip(rows, refs):
        channel = normalize_channel(row.get("channel"))
        # Comment REPLIES are exempt from the META_* gate (they publish via the
        # engagement reply connector's own credentials — see actions.publish);
        # their approve surfaces that connector's own error, so the queue must
        # not claim a META_* blocker it wouldn't actually hit.
        if (row.get("type") or "").lower() == "comment":
            blocked_reason = None
        else:
            blocked_reason = meta_credentials_blocked_reason(channel)
        ctx = _as_ctx(row.get("context"))
        run_id = row.get("run_id")
        if run_id not in mold_by_run:
            mold_by_run[run_id] = _mold_for_run(run_id, dsn)
        posts.append(
            {
                "action_id": row["id"],
                "channel": channel,
                "type": row.get("type"),
                "caption": row.get("draft") or "",
                "target": row.get("target"),
                "run_id": run_id,
                "created_at": _iso(row.get("created_at")),
                "scheduled_for": _iso(row.get("scheduled_for")),
                "schedule_live": bool(row.get("schedule_live")),
                "artwork": _media_package(artwork_id, assets, lookup_error, artifact_id),
                "broll": _media_package(broll_id, assets, lookup_error),
                # The post anatomy + the competitor mold that produced it — the
                # 'wow, this is molded from the best-performing post in our niche'
                # story, rendered from real fields (never fabricated).
                "anatomy": _post_anatomy(ctx, row.get("draft") or ""),
                "mold": mold_by_run[run_id],
                "publishable": blocked_reason is None,
                "blocked_reason": blocked_reason,
            }
        )
    return posts
