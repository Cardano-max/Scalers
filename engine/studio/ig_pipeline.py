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


def run_deep_social_research(
    styles: list[str] | None = None, *, now: datetime | None = None, limit: int = 4
) -> dict[str, Any]:
    """COMPLEX multi-angle social research (operator's order: not one simple query).
    Three cited-only angles through the live provider registry:

      1. instagram-trends — what's trending for tattoo content right now;
      2. reddit-community — what r/tattoo(s) / tattoo-artist threads say actually
         works on Instagram (``site:reddit.com`` scoped);
      3. hooks-and-formats — proven hook lines / reel formats / posting mechanics
         for tattoo studios.

    Returns ``{angles: [{angle, query, sources, cited, note}], total_cited}``.
    Every source is a verbatim provider hit; a blocked/keyless/empty search is an
    honest ``cited=0`` note — trends and hooks are NEVER invented."""
    now = now or datetime.now(timezone.utc)
    month = now.strftime("%B %Y")
    style_bit = " ".join((styles or [])[:3])
    queries = [
        ("instagram-trends", f"tattoo Instagram trends {month} {style_bit}".strip()),
        ("reddit-community",
         f"site:reddit.com tattoo artist instagram what works posts {style_bit}".strip()),
        ("hooks-and-formats",
         "tattoo studio instagram reel hooks caption formats that get bookings"),
    ]
    angles = [dict(run_trend_research(q, limit=limit), angle=name) for name, q in queries]
    return {"angles": angles, "total_cited": sum(a.get("cited") or 0 for a in angles)}


def render_deep_research_block(deep: dict[str, Any]) -> str:
    """Brief block for the multi-angle research — cited snippets grouped per angle,
    or the honest empty statement. The drafter may reference ONLY what is cited."""
    angles = deep.get("angles") or []
    if not deep.get("total_cited"):
        notes = "; ".join(f"{a.get('angle')}: {a.get('note')}" for a in angles)
        return (
            f"\nSOCIAL RESEARCH (3 angles) returned NO usable sources ({notes}). "
            "Do NOT reference or invent any trend, hook pattern, or 'what works' "
            "claim — draft from the artist memory, proven brand patterns, and plan only."
        )
    lines = [
        "\nSOCIAL RESEARCH — LIVE cited sources across three angles (reference a "
        "trend/hook ONLY if it appears below, and cite its URL):"
    ]
    for a in angles:
        if not a.get("cited"):
            lines.append(f"  [{a.get('angle')}] no sources ({a.get('note')})")
            continue
        lines.append(f"  [{a.get('angle')}] query {a.get('query')!r}:")
        for s in (a.get("sources") or [])[:4]:
            title = (s.get("title") or "").strip()[:90]
            snippet = (s.get("snippet") or "").strip()[:160]
            lines.append(f"    - {title} — {snippet} ({s.get('url')})")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Proven brand patterns + b-roll (REAL past campaigns + artist memory).
# --------------------------------------------------------------------------- #
def load_brand_patterns(
    tenant_id: str, artist: str | None, *, dsn: str | None = None
) -> dict[str, Any]:
    """The artist's PROVEN voice: hooks/CTAs from real past campaigns
    (campaign_examples) + brand_voice / style_profile artist memories. All live
    reads; honest empties."""
    out: dict[str, Any] = {"campaign_ctas": [], "campaign_hooks": [], "voice_memories": []}
    try:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(_dsn(dsn), row_factory=dict_row, connect_timeout=5) as conn:
            rows = conn.execute(
                "SELECT campaign_name, cta, message_copy, offer_price_usd, offer_type, "
                "recipient_count, delivered_count FROM campaign_examples "
                "WHERE tenant_id=%s AND (%s::text IS NULL OR artist_name ILIKE %s) "
                "ORDER BY created_at DESC LIMIT 5",
                (tenant_id, artist, f"%{artist}%" if artist else None),
            ).fetchall()
        for r in rows:
            if r.get("cta"):
                out["campaign_ctas"].append(
                    {"cta": r["cta"], "campaign": r.get("campaign_name"),
                     "delivered": r.get("delivered_count")})
            first_line = (r.get("message_copy") or "").strip().splitlines()
            if first_line:
                out["campaign_hooks"].append(
                    {"hook": first_line[0][:160], "campaign": r.get("campaign_name"),
                     "offer": f"${r.get('offer_price_usd')} {r.get('offer_type') or ''}".strip()})
    except Exception:
        pass
    if artist:
        try:
            from studio.artist_memory import list_artist_memories
            from studio.artists_directory import artist_slug as _slugify

            for m in list_artist_memories(tenant_id, _slugify(artist), dsn=dsn):
                kind = (m.get("metadata") or {}).get("kind") or ""
                if kind in ("brand_voice", "style_profile"):
                    out["voice_memories"].append(m.get("text") or "")
        except Exception:
            pass
    return out


def render_brand_patterns_block(patterns: dict[str, Any]) -> str:
    """Brief block ordering the drafter to MOLD researched trends into the PROVEN
    brand hooks/angles/CTAs — never generic copy, never an unproven claim."""
    ctas = patterns.get("campaign_ctas") or []
    hooks = patterns.get("campaign_hooks") or []
    voices = patterns.get("voice_memories") or []
    if not (ctas or hooks or voices):
        return (
            "\nPROVEN BRAND PATTERNS: none on file for this artist yet — use the "
            "studio's standard voice; do NOT invent 'proven' claims."
        )
    lines = ["\nPROVEN BRAND PATTERNS (REAL past campaigns + artist memory) — mold the "
             "researched trends INTO these hooks/angles/CTAs; keywords only from cited sources:"]
    for h in hooks[:3]:
        lines.append(f"  - proven hook ({h['campaign']}, {h.get('offer')}): \"{h['hook']}\"")
    for c in ctas[:3]:
        d = f", {c['delivered']} delivered" if c.get("delivered") else ""
        lines.append(f"  - proven CTA ({c['campaign']}{d}): \"{c['cta']}\"")
    for v in voices[:2]:
        lines.append(f"  - voice memory: {v[:220]}")
    return "\n".join(lines)


def load_broll(
    tenant_id: str, artist: str | None, *, dsn: str | None = None
) -> list[dict[str, Any]]:
    """REAL b-roll on file: the artist's VIDEO library assets (media='video').
    Honest empty list when none."""
    try:
        from studio.artwork_select import list_artwork  # noqa: F401  (same store)
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(_dsn(dsn), row_factory=dict_row, connect_timeout=5) as conn:
            # Tenant-scoped like list_artwork (library assets live under
            # campaign_id='portfolio:<tenant>'): without this, a no-artist run
            # would pick the newest video from ANY tenant's library and stamp
            # it onto every staged post as that run's b-roll.
            rows = conn.execute(
                "SELECT id, content FROM assets WHERE content->>'media'='video' "
                "AND campaign_id = %s "
                "AND (%s::text IS NULL OR content->>'artist' ILIKE %s) "
                "ORDER BY created_at DESC LIMIT 5",
                (f"portfolio:{tenant_id}", artist, f"%{artist}%" if artist else None),
            ).fetchall()
        return [
            {"asset_id": r["id"],
             "caption": (r["content"] or {}).get("caption") or "",
             "summary": (r["content"] or {}).get("vlm_summary") or ""}
            for r in rows
        ]
    except Exception:
        return []


def render_broll_block(broll: list[dict[str, Any]]) -> str:
    if not broll:
        return ""
    lines = ["\nB-ROLL ON FILE (REAL videos in the library — reference these for the "
             "reel/post concept; never invent footage):"]
    for b in broll:
        desc = b.get("summary") or b.get("caption") or b.get("asset_id")
        lines.append(f"  - {b['asset_id']}: {str(desc)[:180]}")
    return "\n".join(lines)


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
    competitor_pick: dict[str, Any] | None = None,
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

    # COMPLEX multi-angle research (operator's order): instagram-trends +
    # reddit-community + hooks-and-formats, all cited-only. Recorded as TWO
    # visible crew steps so the panel shows the deep pass distinctly.
    deep = run_deep_social_research(mem.get("styleTags") or None)
    if run_id:
        _record_crew_step(
            dsn, run_id, campaign_id,
            "trend_research", "firecrawl",
            {"queries": [a.get("query") for a in deep.get("angles") or []][:1]},
            (deep.get("angles") or [{}])[0],
        )
        _record_crew_step(
            dsn, run_id, campaign_id,
            "hook_research", "firecrawl",
            {"queries": [a.get("query") for a in (deep.get("angles") or [])[1:]]},
            {"angles": (deep.get("angles") or [])[1:],
             "total_cited": deep.get("total_cited")},
        )

    # Proven brand voice + b-roll from the REAL stores; a visible crew step too.
    patterns = load_brand_patterns(tenant_id, artist, dsn=dsn)
    broll = load_broll(tenant_id, artist, dsn=dsn)
    if run_id:
        _record_crew_step(
            dsn, run_id, campaign_id,
            "brand_patterns", "db",
            {"artist": artist},
            {"proven_ctas": len(patterns.get("campaign_ctas") or []),
             "proven_hooks": len(patterns.get("campaign_hooks") or []),
             "voice_memories": len(patterns.get("voice_memories") or []),
             "broll_on_file": len(broll)},
        )

    # COMPETITOR CREATIVE INTELLIGENCE (operator-uploaded posts, ADDITIVE):
    # the best-scoring competitor post's SHAPE — structure/hook/CTA pattern — as
    # inspiration to MOLD; artwork/wording/offers always stay OURS. Best-effort +
    # honest-empty: with no competitor data the block SAYS so, and no crew step is
    # recorded (no competitor read contributed to this brief). When a pattern
    # exists, the crew step carries the full score breakdown so the live panel
    # shows the reasoning; model is 'db+cell' only when the clamped LLM read ran.
    #
    # When the OPERATOR PICKED a post (the competitor selection pause answered),
    # THAT post is the reference: the MOLD step runs here — after the research
    # crew steps, recorded as the run's one role='molder' agent_run — and its
    # brand-adapted direction REPLACES the auto best_pattern block (one pattern
    # per brief, the operator's, never both).
    competitor_block = ""
    if competitor_pick is not None:
        try:
            from studio.competitor_flow import mold_competitor_pattern, render_molded_block

            mold = mold_competitor_pattern(
                tenant_id, plan, competitor_pick,
                run_id=run_id, campaign_id=campaign_id, dsn=dsn,
            )
            competitor_block = render_molded_block(mold, competitor_pick)
        except Exception:
            competitor_block = ""  # grounding never breaks the run
    else:
        try:
            from studio.competitor_intel import best_pattern, render_competitor_pattern_block

            competitor = best_pattern(tenant_id, artist=artist, dsn=dsn)
            competitor_block = render_competitor_pattern_block(competitor)
            if run_id and competitor:
                _record_crew_step(
                    dsn, run_id, campaign_id,
                    "competitor_intel",
                    "db+cell" if competitor.get("llm_refined") else "db",
                    {"artist": artist, "tenant_id": tenant_id},
                    {"handle": competitor.get("handle"),
                     "url": competitor.get("url"),
                     "total_score": competitor.get("total_score"),
                     "scores": competitor.get("scores"),
                     "hook_line": competitor.get("hook_line"),
                     "emotional_angle": competitor.get("emotional_angle"),
                     "why_it_worked": competitor.get("why_it_worked"),
                     "llm_refined": bool(competitor.get("llm_refined"))},
                )
        except Exception:
            competitor_block = ""  # grounding never breaks the run; block simply absent

    parts = [
        render_artist_memory_block(mem),
        render_deep_research_block(deep),
        render_brand_patterns_block(patterns),
    ]
    if competitor_block:
        parts.append(competitor_block)
    parts.append(render_broll_block(broll))
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
    ``note``). Also lands the flat ``artwork_asset_id`` (+ ``broll_asset_id`` when
    a real video is on file) so the social ready queue can resolve the draft's
    media. Returns the number of rows updated. Best-effort; never sends."""
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
        # Flat mirror of the SAME real asset id under the stable key the social
        # ready queue resolves (studio.social_queue) — one parse path for every
        # draft-creation route, never a second source of truth.
        if artwork.get("assetId"):
            fields["artwork_asset_id"] = artwork.get("assetId")
    # Optional b-roll reference: the newest REAL video asset on file for this
    # artist — the same rows the brief's b-roll block cites. load_broll is
    # honest-empty ([]) when none exist / the store is unavailable, so this key
    # appears only when a real video row backs it.
    broll = load_broll(tenant_id, artist, dsn=dsn)
    if broll:
        fields["broll_asset_id"] = broll[0]["asset_id"]
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
