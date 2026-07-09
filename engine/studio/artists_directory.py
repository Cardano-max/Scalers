"""Artist directory reads — the REAL roster (``artists`` + ``artist_studios``) joined
with what the system actually knows about each artist: portfolio artwork (the
``assets`` library), real past campaigns (``campaign_examples``), and artist
memories (``memories`` with ``subject_type='artist'``).

Every number is a live count; every field comes from a real row. An artist with no
artwork / campaigns / memories reads honest zeros and empty arrays — nothing is
invented. Slugs are deterministic projections of the stored name (``Bryan Alvarez``
-> ``bryan-alvarez``), so the API can address artists without exposing raw ids.
"""

from __future__ import annotations

import os
import re
from typing import Any

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"


def _dsn(dsn: str | None = None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def _connect(dsn: str | None = None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), autocommit=True, row_factory=dict_row)


def artist_slug(name: str) -> str:
    """Deterministic slug for an artist name (mirrors artwork_select's slugging)."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def _artist_rows(tenant_id: str, dsn: str | None = None) -> list[dict[str, Any]]:
    """The tenant's artist rows + their studios, honest-empty on any store failure."""
    try:
        with _connect(dsn) as conn:
            artists = conn.execute(
                "SELECT id, name, email, phone, artist_persona, artist_style_tags "
                "FROM artists WHERE tenant_id=%s AND is_test=FALSE ORDER BY name",
                (tenant_id,),
            ).fetchall()
            studios = conn.execute(
                "SELECT s.artist_id, s.studio_name FROM artist_studios s "
                "JOIN artists a ON a.id = s.artist_id WHERE a.tenant_id=%s",
                (tenant_id,),
            ).fetchall()
    except Exception:
        return []
    by_artist: dict[str, list[str]] = {}
    for s in studios:
        by_artist.setdefault(s["artist_id"], []).append(s["studio_name"])
    out = []
    for a in artists:
        row = dict(a)
        row["slug"] = artist_slug(a["name"])
        row["studios"] = sorted(by_artist.get(a["id"], []))
        out.append(row)
    return out


def resolve_artist(
    tenant_id: str, name_or_slug: str | None, *, dsn: str | None = None
) -> dict[str, Any] | None:
    """Resolve an operator-supplied artist NAME or SLUG against the real roster.
    Case-insensitive; exact name match wins, then slug match, then a unique
    first-name prefix (``'bryan'`` -> Bryan Alvarez only if unambiguous). ``None``
    when nothing matches — the caller records the honest miss, never a made-up
    artist."""
    wanted = (name_or_slug or "").strip()
    if not wanted:
        return None
    rows = _artist_rows(tenant_id, dsn)
    lowered = wanted.lower()
    slugged = artist_slug(wanted)
    for r in rows:  # exact name (case-insensitive)
        if r["name"].strip().lower() == lowered:
            return r
    for r in rows:  # exact slug
        if r["slug"] == slugged:
            return r
    prefix = [r for r in rows if r["slug"].startswith(slugged + "-") or
              r["name"].strip().lower().startswith(lowered + " ")]
    if len(prefix) == 1:
        return prefix[0]
    return None


def _artwork_by_artist(tenant_id: str, dsn: str | None = None) -> dict[str, list[Any]]:
    """The portfolio library grouped by artist slug (real ``assets`` rows only)."""
    from studio.artwork_select import list_artwork

    grouped: dict[str, list[Any]] = {}
    for ref in list_artwork(tenant_id, dsn=dsn):
        grouped.setdefault(artist_slug(ref.artist), []).append(ref)
    return grouped


def _campaign_counts(tenant_id: str, dsn: str | None = None) -> dict[str, int]:
    try:
        with _connect(dsn) as conn:
            rows = conn.execute(
                "SELECT artist_name, count(*) AS n FROM campaign_examples "
                "WHERE tenant_id=%s AND artist_name IS NOT NULL GROUP BY artist_name",
                (tenant_id,),
            ).fetchall()
    except Exception:
        return {}
    return {artist_slug(r["artist_name"]): int(r["n"]) for r in rows}


def _memory_counts(tenant_id: str, dsn: str | None = None) -> dict[str, int]:
    try:
        with _connect(dsn) as conn:
            rows = conn.execute(
                "SELECT COALESCE(subject_id,'') AS sid, count(*) AS n FROM memories "
                "WHERE tenant_id=%s AND subject_type='artist' AND is_test=FALSE "
                "GROUP BY 1",
                (tenant_id,),
            ).fetchall()
    except Exception:
        return {}
    return {r["sid"]: int(r["n"]) for r in rows}


def list_artists(tenant_id: str, *, dsn: str | None = None) -> list[dict[str, Any]]:
    """The roster summary: ``[{slug, name, studios, artworkCount, campaignCount,
    memoryCount}]`` — every count a live read, honest zeros where nothing exists."""
    artworks = _artwork_by_artist(tenant_id, dsn)
    campaigns = _campaign_counts(tenant_id, dsn)
    memories = _memory_counts(tenant_id, dsn)
    out: list[dict[str, Any]] = []
    for r in _artist_rows(tenant_id, dsn):
        slug = r["slug"]
        out.append(
            {
                "slug": slug,
                "name": r["name"],
                "studios": r["studios"],
                "artworkCount": len(artworks.get(slug, [])),
                "campaignCount": campaigns.get(slug, 0),
                "memoryCount": memories.get(slug, 0),
            }
        )
    return out


def _artwork_entries(tenant_id: str, slug: str, dsn: str | None = None) -> list[dict[str, Any]]:
    """The artist's portfolio pieces as API entries — reads the raw asset rows so the
    upload-only fields (vlm_summary / artifact_id) surface. Honest-empty on failure."""
    try:
        from team.store import TeamStore

        from studio.artwork_select import ARTWORK_ASSET_TYPE, _portfolio_campaign_id

        rows = TeamStore(_dsn(dsn)).list_assets(_portfolio_campaign_id(tenant_id))
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if (row.get("asset_type") or "") != ARTWORK_ASSET_TYPE:
            continue
        c = row.get("content") or {}
        if not isinstance(c, dict):
            continue
        if artist_slug(str(c.get("artist") or "")) != slug:
            continue
        out.append(
            {
                "assetId": str(row.get("id") or ""),
                "artifactId": c.get("artifact_id"),
                "styles": [s for s in (c.get("styles") or []) if isinstance(s, str)],
                "motifs": [m for m in (c.get("motifs") or []) if isinstance(m, str)],
                "vlmSummary": str(c.get("vlm_summary") or "") or None,
                "why": None,
            }
        )
    return out


def get_artist_detail(
    tenant_id: str, slug: str, *, dsn: str | None = None
) -> dict[str, Any] | None:
    """One artist's full REAL record, or ``None`` when the slug matches nobody.

    ``styleTags`` are aggregated from the artist's artwork VLM tags (upload-analyzed
    pieces carrying a ``vlm_summary``) — ``[]`` when no VLM-tagged artwork exists
    (never guessed from the name). Campaigns are the artist's real
    ``campaign_examples`` rows; memories are the real ``subject_type='artist'``
    rows, newest first."""
    roster = {r["slug"]: r for r in _artist_rows(tenant_id, dsn)}
    row = roster.get(slug)
    if row is None:
        return None

    artworks = _artwork_entries(tenant_id, slug, dsn)
    style_tags: list[str] = []
    seen: set[str] = set()
    for a in artworks:
        if not a.get("vlmSummary"):
            continue  # styleTags come from REAL VLM-tagged artwork only
        for s in a["styles"]:
            key = s.strip().lower()
            if key and key not in seen:
                seen.add(key)
                style_tags.append(s)

    campaigns: list[dict[str, Any]] = []
    try:
        from studio.campaign_examples_store import get_examples

        for ex in get_examples(tenant_id, artist=row["name"], dsn=dsn):
            price = ex.get("offer_price_usd")
            campaigns.append(
                {
                    "name": ex.get("campaign_name"),
                    "offer_price_usd": float(price) if price is not None else None,
                    "message_copy": ex.get("message_copy"),
                    "cta": ex.get("cta"),
                    "sent_at": ex.get("sent_at"),
                    "delivered_count": ex.get("delivered_count"),
                    "failed_count": ex.get("failed_count"),
                    "dnd_blocked_count": ex.get("dnd_blocked_count"),
                }
            )
    except Exception:
        campaigns = []

    memories: list[dict[str, Any]] = []
    try:
        from studio.artist_memory import list_artist_memories

        for m in list_artist_memories(tenant_id, slug, dsn=dsn):
            memories.append({"at": m.get("at"), "text": m.get("text")})
    except Exception:
        memories = []

    return {
        "slug": slug,
        "name": row["name"],
        "email": row.get("email"),
        "phone": row.get("phone"),
        "studios": row["studios"],
        "styleTags": style_tags,
        "artworks": artworks,
        "campaigns": campaigns,
        "memories": memories,
    }
