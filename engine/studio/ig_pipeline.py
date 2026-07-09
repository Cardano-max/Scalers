"""Instagram pipeline DEPTH (engine-core item 6, spec §11/12/16/21).

The compose spine (archetypes/compose.py — not owned here) drafts IG posts
generically. This module makes the supervisor's Instagram run GENUINELY
channel-specific:

  * :func:`build_ig_brief_block` — a compact grounded block appended to the brief
    passed to ``run_and_trace``: the artist's REAL memory (roster profile + real
    past campaigns + real artwork tags + recent artist memories, each clearly
    marked REAL DATA and honest-empty when missing) plus LIVE trend research via
    the wired Firecrawl provider ("tattoo Instagram trends {month year} {styles}"),
    injected as cited snippets WITH URLs — never fabricated trends; when research
    returns nothing the brief says so.
  * The block's inputs are recorded as REAL channel-crew ``agent_runs``
    (``role='artist_memory' model='db'``, ``role='trend_research'
    model='firecrawl'``) with deterministic ids, so the operator SEES a different
    agent team for Instagram, live — and a resume after the artwork pause
    re-records them as no-ops.
  * :func:`enrich_post_actions` — after staging, every post action's context
    carries artist + the operator-selected artwork + grounded hashtags/CTA
    (from :func:`studio.post_campaign.voice_post_fields`).

HONESTY: every line traces to a real row / provider hit; empty sources are stated
empty. Nothing here sends — it grounds and annotates HELD drafts only.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"


def _dsn(dsn: str | None = None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


# --------------------------------------------------------------------------- #
# Artist memory grounding.
# --------------------------------------------------------------------------- #
def load_artist_memory(
    tenant_id: str, artist: str | None, *, dsn: str | None = None
) -> dict[str, Any]:
    """The artist's REAL memory bundle: roster profile, real past campaigns
    (campaign_examples), library artwork tags, recent artist memories. Every field
    a live read; ``{}``-ish honest empties when the plan names no/unknown artist."""
    out: dict[str, Any] = {
        "artist": None, "slug": None, "resolved": False, "studios": [],
        "styleTags": [], "artworkCount": 0, "campaigns": [], "memories": [],
    }
    if not (artist or "").strip():
        return out
    try:
        from studio.artists_directory import get_artist_detail, resolve_artist

        resolved = resolve_artist(tenant_id, artist, dsn=dsn)
        if resolved is None:
            out["artist"] = artist
            return out
        detail = get_artist_detail(tenant_id, resolved["slug"], dsn=dsn) or {}
    except Exception:
        out["artist"] = artist
        return out
    # Aggregate style tags across ALL library pieces (first-party CSV tags count
    # here too — this is grounding, not the VLM-only styleTags contract).
    styles: list[str] = []
    seen: set[str] = set()
    for w in detail.get("artworks", []):
        for s in w.get("styles") or []:
            key = s.strip().lower()
            if key and key not in seen:
                seen.add(key)
                styles.append(s)
    out.update(
        {
            "artist": detail.get("name"),
            "slug": detail.get("slug"),
            "resolved": True,
            "studios": detail.get("studios", []),
            "styleTags": styles,
            "artworkCount": len(detail.get("artworks", [])),
            "campaigns": [
                {
                    "name": c.get("name"),
                    "offer_price_usd": c.get("offer_price_usd"),
                    "cta": c.get("cta"),
                    "sent_at": c.get("sent_at"),
                    "delivered_count": c.get("delivered_count"),
                    "message_excerpt": (c.get("message_copy") or "")[:180],
                }
                for c in detail.get("campaigns", [])[:5]
            ],
            "memories": detail.get("memories", [])[:5],
        }
    )
    return out


def render_artist_memory_block(mem: dict[str, Any]) -> str:
    """The brief block for the artist memory — REAL DATA marked, honest-empty."""
    if not mem.get("artist"):
        return (
            "\nARTIST MEMORY (REAL DATA): the plan names no artist — no artist "
            "grounding available; write for the studio generally, never invent an "
            "artist."
        )
    if not mem.get("resolved"):
        return (
            f"\nARTIST MEMORY (REAL DATA): {mem['artist']!r} matches nobody in the "
            "roster — no profile/campaign/artwork facts exist for that name. Do NOT "
            "invent any."
        )
    lines = [
        f"\nARTIST MEMORY — REAL DATA for {mem['artist']} (live from the studio DB; "
        "ground every artist claim here and nowhere else):",
        f"- studios: {', '.join(mem['studios']) or '(none on file)'}",
        f"- artwork on file: {mem['artworkCount']} piece(s)"
        + (f"; real style tags: {', '.join(mem['styleTags'])}" if mem["styleTags"]
           else " (no style tags yet)"),
    ]
    if mem["campaigns"]:
        lines.append("- REAL past campaigns (facts, not targets):")
        for c in mem["campaigns"]:
            price = f" ${c['offer_price_usd']:.0f}" if c.get("offer_price_usd") else ""
            lines.append(
                f"    - {c['name']}{price} (sent {c.get('sent_at') or '?'}; "
                f"delivered {c.get('delivered_count') if c.get('delivered_count') is not None else '?'}) "
                f"CTA: {c.get('cta') or '(none recorded)'}"
            )
    else:
        lines.append("- past campaigns: none on file for this artist (do not invent one)")
    if mem["memories"]:
        lines.append("- recent artist memory (newest first):")
        for m in mem["memories"]:
            lines.append(f"    - [{m.get('at')}] {m.get('text')}")
    else:
        lines.append("- artist memory: none recorded yet")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Trend research (Firecrawl, cited-only).
# --------------------------------------------------------------------------- #
def trend_query(styles: list[str] | None = None, *, now: datetime | None = None) -> str:
    """'tattoo Instagram trends {month year} {styles}' — the live research query."""
    now = now or datetime.now(timezone.utc)
    style_bit = " ".join((styles or [])[:3])
    return f"tattoo Instagram trends {now.strftime('%B %Y')} {style_bit}".strip()


def run_trend_research(query: str, *, limit: int = 5) -> dict[str, Any]:
    """LIVE trend research through the wired provider registry (Firecrawl — enabled
    only when its key is armed). Returns ``{query, sources:[{title, snippet, url}],
    cited, note}``. HONESTY: every field is verbatim from a real provider hit; a
    keyless/failed/empty search returns cited=0 with the concrete note — trends are
    NEVER fabricated."""
    try:
        from research.pipeline import live_registry

        provider = live_registry().get("firecrawl")
        if provider is None or not getattr(provider, "enabled", False):
            return {
                "query": query, "sources": [], "cited": 0,
                "note": "trend research unavailable (no Firecrawl key armed)",
            }
        sources: list[dict[str, Any]] = []
        seen: set[str] = set()
        for hit in provider.search(query, limit=limit):
            url = getattr(hit, "url", None)
            if not url or url in seen:
                continue
            seen.add(url)
            sources.append(
                {
                    "title": getattr(hit, "title", None),
                    "snippet": getattr(hit, "snippet", None),
                    "url": url,
                }
            )
        if not sources:
            return {
                "query": query, "sources": [], "cited": 0,
                "note": "trend research ran but returned no sources",
            }
        return {"query": query, "sources": sources, "cited": len(sources), "note": None}
    except Exception as exc:
        return {
            "query": query, "sources": [], "cited": 0,
            "note": f"trend research failed: {type(exc).__name__}",
        }


def render_trend_block(research: dict[str, Any]) -> str:
    """The brief block for trend research — cited snippets with URLs, or the honest
    empty statement (never invented trends)."""
    if not research.get("cited"):
        return (
            f"\nINSTAGRAM TREND RESEARCH: query {research.get('query')!r} returned "
            f"NO usable sources ({research.get('note')}). Do NOT reference or invent "
            "any trend — draft from the artist memory and plan only."
        )
    lines = [
        f"\nINSTAGRAM TREND RESEARCH — LIVE cited sources for {research.get('query')!r} "
        "(reference a trend ONLY if it appears below, and cite its URL):",
    ]
    for s in research["sources"]:
        title = (s.get("title") or "").strip() or "(untitled)"
        snippet = (s.get("snippet") or "").strip()[:220]
        lines.append(f"- {title} — {snippet} [{s.get('url')}]")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The channel-crew trace + brief assembly.
# --------------------------------------------------------------------------- #
def _record_crew_step(
    dsn: str | None,
    run_id: str,
    campaign_id: str | None,
    role: str,
    model: str,
    inp: dict[str, Any],
    out: dict[str, Any],
) -> None:
    """Record one IG-crew agent_run with a DETERMINISTIC id (resume-safe; the store
    is ON CONFLICT DO NOTHING). Best-effort — grounding never breaks the run."""
    try:
        from team.store import TeamStore

        ts = TeamStore(_dsn(dsn))
        ts.setup()
        ts.record_agent_run(
            id=f"ar_{role}_{hashlib.sha1(run_id.encode()).hexdigest()[:16]}",
            campaign_id=campaign_id or "",
            run_id=run_id,
            role=role,
            model=model,
            input=inp,
            output=out,
        )
    except Exception:
        pass


def build_ig_brief_block(
    plan: Any,
    tenant_id: str,
    *,
    run_id: str | None = None,
    campaign_id: str | None = None,
    artwork: dict[str, Any] | None = None,
    artwork_note: str | None = None,
    dsn: str | None = None,
) -> str:
    """Assemble the IG-specific grounded brief block AND record the channel crew's
    real steps (``artist_memory`` / ``trend_research`` agent_runs) so the Instagram
    run visibly carries a different agent team. Returns the text to append to the
    compose brief."""
    artist = (getattr(plan, "artist", "") or "").strip() or None
    mem = load_artist_memory(tenant_id, artist, dsn=dsn)
    if run_id:
        _record_crew_step(
            dsn, run_id, campaign_id,
            "artist_memory", "db",
            {"artist": artist, "tenant_id": tenant_id},
            {
                "resolved": mem.get("resolved"),
                "artist": mem.get("artist"),
                "studios": mem.get("studios"),
                "style_tags": mem.get("styleTags"),
                "artwork_count": mem.get("artworkCount"),
                "campaigns": mem.get("campaigns"),
                "memories": mem.get("memories"),
            },
        )

    query = trend_query(mem.get("styleTags") or None)
    research = run_trend_research(query)
    if run_id:
        _record_crew_step(
            dsn, run_id, campaign_id,
            "trend_research", "firecrawl",
            {"query": query},
            research,
        )

    parts = [render_artist_memory_block(mem), render_trend_block(research)]
    if artwork:
        parts.append(
            "\nSELECTED ARTWORK (operator-picked, REAL): asset "
            f"{artwork.get('assetId')}"
            + (f" — {artwork.get('vlmSummary')}" if artwork.get("vlmSummary") else "")
            + (
                f"; tags: {', '.join(artwork.get('styles') or [])}"
                if artwork.get("styles")
                else ""
            )
            + ". The post presents THIS piece; describe only what its tags/summary "
            "state."
        )
    elif artwork_note:
        parts.append(f"\nARTWORK: {artwork_note} — do not describe or invent an image.")
    return "\n" + "\n".join(p.strip("\n") for p in parts) + "\n"


# --------------------------------------------------------------------------- #
# Post-staging context enrichment.
# --------------------------------------------------------------------------- #
def enrich_post_actions(
    run_id: str,
    tenant_id: str,
    *,
    artist: str | None = None,
    artwork: dict[str, Any] | None = None,
    theme: str | None = None,
    dsn: str | None = None,
) -> int:
    """Land artist + artwork + grounded hashtags/CTA on every staged POST action of
    ``run_id`` (context JSON, merged — an existing text context is preserved under
    ``note``). Returns the number of rows updated. Best-effort; never sends."""
    import psycopg

    fields: dict[str, Any] = {}
    if artist:
        fields["artist"] = artist
    if artwork:
        fields["artwork"] = {
            "assetId": artwork.get("assetId"),
            "artifactId": artwork.get("artifactId"),
            "vlmSummary": artwork.get("vlmSummary"),
        }
    try:
        from studio.post_campaign import voice_post_fields

        voice_fields = voice_post_fields(
            tenant_id,
            styles=(artwork or {}).get("styles") or [],
            motifs=(artwork or {}).get("motifs") or [],
            theme=theme,
        )
        fields.update(voice_fields)
    except Exception:
        pass
    if not fields:
        return 0

    updated = 0
    try:
        with psycopg.connect(_dsn(dsn), autocommit=True) as conn:
            rows = conn.execute(
                "SELECT id, context FROM actions WHERE run_id=%s AND type='post'",
                (run_id,),
            ).fetchall()
            for action_id, context in rows:
                merged: dict[str, Any]
                if context:
                    try:
                        parsed = json.loads(context)
                        merged = parsed if isinstance(parsed, dict) else {"note": context}
                    except Exception:
                        merged = {"note": context}
                else:
                    merged = {}
                merged.update(fields)
                conn.execute(
                    "UPDATE actions SET context=%s, updated_at=now() WHERE id=%s",
                    (json.dumps(merged), action_id),
                )
                updated += 1
    except Exception:
        return updated
    return updated
