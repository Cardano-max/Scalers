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


def _context_refs(raw: Any) -> tuple[str | None, str | None]:
    """``(artwork_asset_id, broll_asset_id)`` referenced by an action's context.

    Reads the enriched JSON context first (``artwork_asset_id``, then the nested
    ``artwork.assetId``/``asset_id``; ``broll_asset_id``), and falls back to the
    legacy text note's ``(asset …)`` marker. Absent/unparseable context degrades
    to ``(None, None)`` — a draft that referenced nothing resolves nothing."""
    if not raw:
        return None, None
    ctx: dict[str, Any] = {}
    if isinstance(raw, dict):
        ctx = raw
    else:
        try:
            parsed = json.loads(raw)
            ctx = parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError, ValueError):
            m = _LEGACY_ASSET_RE.search(str(raw))
            return (m.group(1) if m else None), None

    def _clean(v: Any) -> str | None:
        return v.strip() if isinstance(v, str) and v.strip() else None

    artwork_id = _clean(ctx.get("artwork_asset_id"))
    if artwork_id is None:
        art = ctx.get("artwork")
        if isinstance(art, dict):
            artwork_id = _clean(art.get("assetId")) or _clean(art.get("asset_id"))
    if artwork_id is None:
        m = _LEGACY_ASSET_RE.search(str(ctx.get("note") or ""))
        artwork_id = m.group(1) if m else None
    return artwork_id, _clean(ctx.get("broll_asset_id"))


def _media_package(
    asset_id: str | None,
    assets: dict[str, dict[str, Any]],
    lookup_error: str | None,
) -> dict[str, Any] | None:
    """One resolved media block, or ``None`` when the draft referenced no asset.

    ``found=True`` only when the referenced ``assets`` row really exists; its
    tags/kind/caption are copied verbatim off the row's content. A reference
    whose row is gone (or whose lookup failed) is stated so — never invented."""
    if asset_id is None:
        return None
    if lookup_error is not None:
        return {
            "asset_id": asset_id, "found": False, "media": None,
            "tags": [], "caption": None, "error": lookup_error,
        }
    content = assets.get(asset_id)
    if content is None:
        return {
            "asset_id": asset_id, "found": False, "media": None,
            "tags": [], "caption": None,
            "error": f"referenced asset {asset_id} not found in the library",
        }
    tags: list[str] = []
    seen: set[str] = set()
    for key in ("styles", "motifs"):
        for t in content.get(key) or []:
            if isinstance(t, str) and t.strip() and t.strip().lower() not in seen:
                seen.add(t.strip().lower())
                tags.append(t.strip())
    return {
        "asset_id": asset_id,
        "found": True,
        "media": "video" if content.get("media") == "video" else "image",
        "tags": tags,
        "caption": (content.get("caption") or "").strip() or None,
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
        wanted = sorted({a for pair in refs for a in pair if a})
        assets: dict[str, dict[str, Any]] = {}
        lookup_error: str | None = None
        if wanted:
            try:
                arows = conn.execute(
                    "SELECT id, content FROM assets WHERE id = ANY(%s)", (wanted,)
                ).fetchall()
                assets = {
                    a["id"]: (a["content"] if isinstance(a["content"], dict) else {})
                    for a in arows
                }
            except Exception as exc:  # noqa: BLE001 — reported per-package, never masked
                lookup_error = f"asset lookup failed: {type(exc).__name__}: {exc}"

    posts: list[dict[str, Any]] = []
    for row, (artwork_id, broll_id) in zip(rows, refs):
        channel = normalize_channel(row.get("channel"))
        blocked_reason = meta_credentials_blocked_reason(channel)
        posts.append(
            {
                "action_id": row["id"],
                "channel": channel,
                "type": row.get("type"),
                "caption": row.get("draft") or "",
                "target": row.get("target"),
                "run_id": row.get("run_id"),
                "created_at": _iso(row.get("created_at")),
                "scheduled_for": _iso(row.get("scheduled_for")),
                "schedule_live": bool(row.get("schedule_live")),
                "artwork": _media_package(artwork_id, assets, lookup_error),
                "broll": _media_package(broll_id, assets, lookup_error),
                "publishable": blocked_reason is None,
                "blocked_reason": blocked_reason,
            }
        )
    return posts
