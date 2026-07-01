"""Studio artwork LIBRARY + evidence-grounded artwork selection (P2 studio layer).

The tattoo-studio post-drafting layer needs to attach the *right* piece from the
studio's own portfolio to a post, and show an honest "which artwork & why". This
module is that capability. It is deliberately built on the SAME honesty spine as
:mod:`studio.offers`:

  * The portfolio lives as real rows in the persistent ``assets`` table
    (:class:`team.store.TeamStore`) — ``asset_type="studio_artwork"``,
    ``status="library"`` (a portfolio item, never a queued send), scoped to the
    tenant via ``campaign_id="portfolio:{tenant}"``. Each piece therefore has a
    REAL, traceable ``asset_id``.
  * :func:`seed_studio_artwork` seeds a clearly-labelled MOCK portfolio so the
    workflow runs end-to-end now; the operator (or the P4 Mini-App / artwork CSV)
    REPLACES it with real artwork later — identical workflow, only the source
    changes. This is FIRST-PARTY provided metadata (studio-tagged), NOT a claim
    that a vision model auto-tagged anything: the P4 VLM tagger will *enrich* these
    same rows later. Nothing here fabricates a piece or a tag.
  * :func:`select_artwork` is PURE. It scores each piece by the overlap between its
    OWN stored ``styles`` / ``motifs`` tags and the artist's style + the post
    theme, and :func:`build_why` renders a rationale in which EVERY clause traces
    back to a stored field (the caption, the tags, the asset id). It never invents
    a style, a motif, or an engagement/scarcity claim.
  * When an artist has no artwork on file the read is honestly empty and selection
    returns ``None`` — the caller then says "no artwork available", it does not
    invent a picture.

The drafter (:mod:`studio.post_campaign`) is the only writer of side-effecting
(HELD) actions; this module only reads/seeds the library and reasons over it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

# Portfolio rows in the shared ``assets`` table use this type + status so they are
# never confused with a team-produced, queued send (status defaults to 'queued').
ARTWORK_ASSET_TYPE = "studio_artwork"
ARTWORK_STATUS = "library"

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"


def _dsn(dsn: str | None = None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def _portfolio_campaign_id(tenant_id: str) -> str:
    """The ``assets.campaign_id`` bucket that holds one tenant's portfolio."""
    return f"portfolio:{tenant_id}"


def _norm(term: str) -> str:
    """Canonical token for overlap matching: lowercase, non-alphanumerics dropped so
    'fine-line', 'fine line' and 'fineline' all compare equal. Pure."""
    return re.sub(r"[^a-z0-9]", "", (term or "").lower())


def _norm_set(terms: list[str] | tuple[str, ...] | None) -> set[str]:
    return {n for n in (_norm(t) for t in (terms or [])) if n}


# --------------------------------------------------------------------------- #
# Normalized domain models.
# --------------------------------------------------------------------------- #
@dataclass
class ArtworkRef:
    """One portfolio piece, normalized from an ``assets`` row. ``styles``/``motifs``
    are the studio's own tags (seed/CSV/operator today, VLM-enriched in P4)."""

    asset_id: str
    artist: str
    image_ref: str
    caption: str
    styles: list[str] = field(default_factory=list)
    motifs: list[str] = field(default_factory=list)
    is_best_example: bool = False
    source: str = "seed"

    @classmethod
    def from_asset_row(cls, row: dict[str, Any]) -> "ArtworkRef | None":
        """Normalize an ``assets`` row (content is JSONB) into an :class:`ArtworkRef`,
        or ``None`` if the row is not a studio-artwork row. Never fabricates missing
        fields — an absent caption stays empty, absent tags stay ``[]``."""
        if (row.get("asset_type") or "") != ARTWORK_ASSET_TYPE:
            return None
        c = row.get("content") or {}
        if not isinstance(c, dict):
            return None
        return cls(
            asset_id=str(row.get("id") or ""),
            artist=str(c.get("artist") or "").strip(),
            image_ref=str(c.get("image_ref") or "").strip(),
            caption=str(c.get("caption") or "").strip(),
            styles=[s for s in (c.get("styles") or []) if isinstance(s, str) and s.strip()],
            motifs=[m for m in (c.get("motifs") or []) if isinstance(m, str) and m.strip()],
            is_best_example=bool(c.get("is_best_example")),
            source=str(c.get("source") or "seed"),
        )


@dataclass
class ArtworkPick:
    """The chosen piece + the EVIDENCE for choosing it. ``matched_styles`` /
    ``matched_motifs`` are the exact stored tags that drove the match (empty on a
    portfolio fallback); ``why`` is a grounded rationale; ``exact_match`` is False
    when nothing overlapped and we honestly fell back to a portfolio piece."""

    asset_id: str
    artist: str
    image_ref: str
    caption: str
    matched_styles: list[str]
    matched_motifs: list[str]
    score: int
    exact_match: bool
    why: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "artist": self.artist,
            "image_ref": self.image_ref,
            "caption": self.caption,
            "matched_styles": list(self.matched_styles),
            "matched_motifs": list(self.matched_motifs),
            "score": self.score,
            "exact_match": self.exact_match,
            "why": self.why,
        }


# --------------------------------------------------------------------------- #
# Reads over the asset library.
# --------------------------------------------------------------------------- #
def list_artwork(
    tenant_id: str, artist: str | None = None, *, dsn: str | None = None
) -> list[ArtworkRef]:
    """Portfolio pieces for the tenant (optionally one artist), oldest first.

    Honest-empty (``[]``) when there is no portfolio yet OR the store is unavailable —
    the caller then says "no artwork available" rather than inventing a picture. Never
    fabricates a piece. Best-effort: a store hiccup yields ``[]``."""
    try:
        from team.store import TeamStore

        rows = TeamStore(_dsn(dsn)).list_assets(_portfolio_campaign_id(tenant_id))
    except Exception:
        return []
    want = _norm(artist) if artist else None
    out: list[ArtworkRef] = []
    for row in rows:
        ref = ArtworkRef.from_asset_row(row)
        if ref is None:
            continue
        if want is not None and _norm(ref.artist) != want:
            continue
        out.append(ref)
    return out


def artist_styles(artworks: list[ArtworkRef]) -> list[str]:
    """The artist's style set DERIVED from their own portfolio (one source of truth —
    no invented roster). De-duped by canonical token, first-seen casing kept."""
    seen: set[str] = set()
    out: list[str] = []
    for a in artworks:
        for s in a.styles:
            n = _norm(s)
            if n and n not in seen:
                seen.add(n)
                out.append(s)
    return out


# --------------------------------------------------------------------------- #
# Selection + the grounded "why".
# --------------------------------------------------------------------------- #
def _overlap(tags: list[str], compare: set[str]) -> list[str]:
    """The ORIGINAL-cased ``tags`` whose canonical token is in ``compare``. Order and
    casing preserved for display; every returned token is a real stored tag."""
    out: list[str] = []
    seen: set[str] = set()
    for t in tags:
        n = _norm(t)
        if n and n in compare and n not in seen:
            seen.add(n)
            out.append(t)
    return out


def select_artwork(
    artworks: list[ArtworkRef],
    *,
    artist_styles: list[str] | None = None,
    theme_terms: list[str] | None = None,
) -> ArtworkPick | None:
    """The best REAL portfolio piece for this artist + theme, or ``None`` when the
    library is empty (never invented).

    Score = 2·(style tags matching the artist's style OR the theme) + 1·(motif tags
    matching the theme) + 1 if flagged the artist's best example. Deterministic
    tie-break: higher score, then best-example, then a stable asset-id order — so the
    same inputs always pick the same piece (needed for exactly-once staging).

    If the top piece has NO tag overlap at all it is still returned, but as an honest
    PORTFOLIO FALLBACK (``exact_match=False``) — the "why" says so rather than
    claiming a match that is not there."""
    if not artworks:
        return None

    style_cmp = _norm_set(artist_styles) | _norm_set(theme_terms)
    theme_cmp = _norm_set(theme_terms)

    scored: list[tuple[int, bool, str, ArtworkRef, list[str], list[str]]] = []
    for a in artworks:
        matched_styles = _overlap(a.styles, style_cmp)
        matched_motifs = _overlap(a.motifs, theme_cmp)
        score = 2 * len(matched_styles) + len(matched_motifs) + (1 if a.is_best_example else 0)
        scored.append((score, a.is_best_example, a.asset_id, a, matched_styles, matched_motifs))

    # Best: highest score, then best-example, then stable asset-id (ascending) so the
    # choice is fully deterministic.
    scored.sort(key=lambda t: (-t[0], not t[1], t[2]))
    score, _best, _aid, art, matched_styles, matched_motifs = scored[0]
    exact = bool(matched_styles or matched_motifs)
    pick = ArtworkPick(
        asset_id=art.asset_id,
        artist=art.artist,
        image_ref=art.image_ref,
        caption=art.caption,
        matched_styles=matched_styles,
        matched_motifs=matched_motifs,
        score=score,
        exact_match=exact,
        why="",
    )
    pick.why = build_why(pick)
    return pick


def build_why(pick: ArtworkPick) -> str:
    """A grounded, human rationale for the pick. EVERY concrete token here is a stored
    field (the caption, a matched tag, the asset id) — nothing is invented. Used by the
    preview panel and the review-queue context."""
    caption = pick.caption or "(untitled piece)"
    artist = pick.artist or "the artist"
    if pick.exact_match:
        bits: list[str] = []
        if pick.matched_styles:
            bits.append(
                f"tagged {_join(pick.matched_styles)} — matching {artist}'s style"
            )
        if pick.matched_motifs:
            bits.append(f"its {_join(pick.matched_motifs)} motif fits this post")
        reason = "; ".join(bits) if bits else "matches this post"
        return (
            f'Picked "{caption}" because it is {reason}. '
            f"This traces to the piece's own portfolio tags (asset {pick.asset_id})."
        )
    # No overlap: be honest that this is a representative portfolio piece, not a match.
    return (
        f'No tagged style or motif overlaps this theme, so showing {artist}\'s '
        f'portfolio piece "{caption}" (asset {pick.asset_id}). No match was claimed.'
    )


def _join(items: list[str]) -> str:
    """'a', 'a and b', 'a, b and c' — no Oxford drama, no rule-of-three padding."""
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


# --------------------------------------------------------------------------- #
# Seed — a realistic MOCK portfolio so the workflow runs end-to-end now. The operator
# (or the P4 Mini-App / artwork CSV) REPLACES these with real artwork later; the P4
# VLM tagger enriches the same rows. Metadata here is FIRST-PARTY (studio-provided),
# not a fabricated vision-model output. Idempotent: deterministic ids + record_asset's
# ON CONFLICT (id) DO NOTHING make re-seeding a no-op.
# --------------------------------------------------------------------------- #
SEED_ARTWORK: dict[str, list[dict[str, Any]]] = {
    "Maya": [
        {
            "image_ref": "seed://ladies8391/maya/fine-line-peony.png",
            "caption": "Fine-line peony on the forearm",
            "styles": ["fine-line", "floral"],
            "motifs": ["peony", "botanical"],
            "is_best_example": True,
        },
        {
            "image_ref": "seed://ladies8391/maya/single-needle-lavender.png",
            "caption": "Single-needle lavender sprig",
            "styles": ["fine-line", "floral"],
            "motifs": ["lavender", "botanical"],
        },
        {
            "image_ref": "seed://ladies8391/maya/minimal-wave.png",
            "caption": "Minimal fine-line wave",
            "styles": ["fine-line", "minimalist"],
            "motifs": ["wave", "linework"],
        },
    ],
    "Rae": [
        {
            "image_ref": "seed://ladies8391/rae/neo-traditional-rose.png",
            "caption": "Neo-traditional rose in bold color",
            "styles": ["neo-traditional", "floral", "color"],
            "motifs": ["rose", "botanical"],
            "is_best_example": True,
        },
        {
            "image_ref": "seed://ladies8391/rae/fine-line-script.png",
            "caption": "Fine-line script lettering",
            "styles": ["script", "fine-line", "lettering"],
            "motifs": ["script", "lettering"],
        },
        {
            "image_ref": "seed://ladies8391/rae/neo-traditional-coverup-bloom.png",
            "caption": "Neo-traditional floral cover-up",
            "styles": ["neo-traditional", "cover-up", "floral"],
            "motifs": ["bloom", "cover-up"],
        },
    ],
    "Noor": [
        {
            "image_ref": "seed://ladies8391/noor/blackwork-fern.png",
            "caption": "Blackwork fern half-sleeve",
            "styles": ["blackwork", "sleeve"],
            "motifs": ["fern", "botanical"],
            "is_best_example": True,
        },
        {
            "image_ref": "seed://ladies8391/noor/geometric-blackwork.png",
            "caption": "Geometric blackwork forearm",
            "styles": ["blackwork", "geometric"],
            "motifs": ["geometric", "linework"],
        },
    ],
}


def _artist_slug(artist: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", artist.lower()).strip("-")


def _seed_asset_id(tenant_id: str, artist: str, index: int) -> str:
    """Deterministic id so re-seeding is a true no-op (ON CONFLICT DO NOTHING)."""
    return f"art_{tenant_id}_{_artist_slug(artist)}_{index:02d}"


def seed_studio_artwork(tenant_id: str = "ladies8391", *, dsn: str | None = None) -> list[str]:
    """Idempotently seed the MOCK studio portfolio into the ``assets`` table. Returns
    the seeded asset ids (deterministic). Best-effort: returns ``[]`` honestly if the
    store is unavailable — it never fabricates a persisted piece."""
    try:
        from team.store import TeamStore

        store = TeamStore(_dsn(dsn))
        store.setup()  # idempotent CREATE TABLE IF NOT EXISTS
    except Exception:
        return []
    campaign_id = _portfolio_campaign_id(tenant_id)
    ids: list[str] = []
    for artist, pieces in SEED_ARTWORK.items():
        for i, piece in enumerate(pieces):
            aid = _seed_asset_id(tenant_id, artist, i)
            content = {
                "artist": artist,
                "image_ref": piece["image_ref"],
                "caption": piece["caption"],
                "styles": piece.get("styles", []),
                "motifs": piece.get("motifs", []),
                "is_best_example": bool(piece.get("is_best_example")),
                "source": "seed",  # honest: MOCK portfolio, replaced by real artwork later
            }
            try:
                store.record_asset(
                    id=aid,
                    campaign_id=campaign_id,
                    asset_type=ARTWORK_ASSET_TYPE,
                    content=content,
                    status=ARTWORK_STATUS,
                )
                ids.append(aid)
            except Exception:
                continue
    return ids


if __name__ == "__main__":  # pragma: no cover
    import json

    tid = os.environ.get("STUDIO_TENANT_ID", "ladies8391")
    seeded = seed_studio_artwork(tid)
    print(json.dumps({"tenant": tid, "seeded_asset_ids": seeded}, indent=2))
