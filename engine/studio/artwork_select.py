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
    """The ``assets.campaign_id`` bucket that holds one tenant's portfolio.

    The tenant is STRIPPED. A trailing space (cmd.exe's `set VAR=x && …` captures the
    space before the `&&`) makes this key miss by one character, the portfolio reads back
    empty, the artwork gate honestly reports "no artwork in the library", and the run
    ships an Instagram post with no image — no error, anywhere. Whitespace must never be
    the difference between a post having a picture and not."""
    return f"portfolio:{(tenant_id or '').strip()}"


def _norm(term: str) -> str:
    """Canonical token for overlap matching: lowercase, non-alphanumerics dropped so
    'fine-line', 'fine line' and 'fineline' all compare equal. Pure."""
    return re.sub(r"[^a-z0-9]", "", (term or "").lower())


def _norm_set(terms: list[str] | tuple[str, ...] | None) -> set[str]:
    return {n for n in (_norm(t) for t in (terms or [])) if n}


#: Lexical bridges between the two vocabularies in play.
#:
#: The operator writes the brief in MARKETING words ("fine-line botanical"); the library
#: is tagged in DESCRIPTIVE words the VLM actually saw ("Dahlia flower", "Sunflowers",
#: "Red roses"). The two share no literal token, so a botanical brief scored ZERO against
#: every botanical piece, the ranking fell back to raw tag count, and a Spider-Man
#: blackwork piece — which carries six style tags — was offered as the top match for
#: "promote Keebs' fine-line botanical work".
#:
#: These are synonyms within one domain, not an ontology: each expansion is a word a
#: tagger would plausibly have used for the SAME subject. Applied to the THEME side only
#: (never to a piece's own tags), so a piece can never be credited with a tag it does not
#: carry, and the "why" still quotes only real stored tags.
_FLORA = (
    "flower", "flowers", "floral", "flora", "botanical", "bloom", "blossom", "petal",
    "dahlia", "sunflower", "sunflowers", "rose", "roses", "peony", "lavender", "orchid",
    "leaf", "leaves", "vine", "plant", "foliage", "fern", "branch", "stem",
)
_FINE_LINE = ("fineline", "linework", "line", "linear", "delicate", "thin", "fine")
_TERM_EXPANSIONS: dict[str, tuple[str, ...]] = {
    **{_norm(k): _FLORA for k in ("botanical", "floral", "flower", "flora", "nature")},
    **{_norm(k): _FINE_LINE for k in ("fineline", "fine", "linework", "delicate")},
}


def _expand_theme(terms: set[str]) -> set[str]:
    """The theme tokens plus their in-domain synonyms. Pure, deterministic."""
    out = set(terms)
    for t in terms:
        for syn in _TERM_EXPANSIONS.get(t, ()):
            n = _norm(syn)
            if n:
                out.add(n)
    return out


#: Brief words that carry no selection signal — they match everything and would let a
#: piece look "more relevant" for answering nothing. Bounded and explicit.
_STOP_TERMS = frozenset(
    {
        _norm(w)
        for w in (
            "the", "and", "for", "with", "our", "your", "this", "that", "from", "into",
            "post", "posts", "page", "session", "sessions", "campaign", "promote",
            "showcase", "about", "book", "booking", "work", "keebs", "tattoo", "tattoos",
        )
    }
)


def _brief_coverage(art: "ArtworkRef", theme_terms: list[str] | None) -> int:
    """How many DISTINCT terms of the operator's brief this piece actually answers.

    This is the relevance metric, and it deliberately is NOT "how many of my tags matched".
    Counting matched tags rewards a heavily-tagged piece for satisfying ONE brief word many
    times: under "dragon blackwork" the Spider-Man video carries six tags containing
    'blackwork' and beat the dragon piece, which answers both halves of the brief. A piece
    that satisfies two of the operator's words is the better answer than one that satisfies
    a single word six times.

    Each term is matched through its own synonym set, so "botanical" is answered by a piece
    tagged "Dahlia flower". Stopwords are ignored — they match everything and mean nothing.
    Pure."""
    covered = 0
    for term in _norm_set(theme_terms):
        if term in _STOP_TERMS or len(term) < 3:
            continue
        syns = {term} | {_norm(s) for s in _TERM_EXPANSIONS.get(term, ())}
        if (
            _overlap(art.styles, syns)
            or _overlap(art.motifs, syns)
            or (_norm(art.collection) and _norm(art.collection) in syns)
        ):
            covered += 1
    return covered


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
    # The flash-concept bucket this piece belongs to (e.g. '4th-of-july', 'pride',
    # 'build-a-pin', 'lunch-menu'). First-party studio metadata like styles/motifs;
    # absent on legacy seed rows (stays ''), so the read contract is unchanged.
    collection: str = ""

    @classmethod
    def from_asset_row(cls, row: dict[str, Any]) -> "ArtworkRef | None":
        """Normalize an ``assets`` row (content is JSONB) into an :class:`ArtworkRef`,
        or ``None`` if the row is not a studio-artwork row. Never fabricates missing
        fields — an absent caption stays empty, absent tags stay ``[]``, an absent
        collection stays ``''`` (older seed rows have none — still valid)."""
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
            collection=str(c.get("collection") or "").strip(),
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
    # The piece's OWN stored tags (superset of the matched subsets) — real metadata the
    # caption + preview render. Never invented; copied straight off the asset row.
    styles: list[str] = field(default_factory=list)
    motifs: list[str] = field(default_factory=list)
    # The piece's own collection tag, plus the collection value that actually matched the
    # post theme (empty when the theme did not name this piece's collection). Both trace
    # to a stored field; the theme/seasonal caption angle reads ``collection``.
    collection: str = ""
    matched_collection: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "artist": self.artist,
            "image_ref": self.image_ref,
            "caption": self.caption,
            "styles": list(self.styles),
            "motifs": list(self.motifs),
            "collection": self.collection,
            "matched_styles": list(self.matched_styles),
            "matched_motifs": list(self.matched_motifs),
            "matched_collection": self.matched_collection,
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
    """The ORIGINAL-cased ``tags`` whose canonical token — or any of whose WORD
    tokens — is in ``compare``. VLM motifs are descriptive phrases ("Liberty Bell",
    "American flag with stars"), so whole-phrase equality alone never matched a
    single theme word like "liberty" or "flag" and every real piece scored zero.
    Word tokens shorter than 3 chars are ignored ("of", "in") so stopwords can't
    manufacture a match. Order and casing preserved for display; every returned
    token is a real stored tag."""
    out: list[str] = []
    seen: set[str] = set()
    for t in tags:
        n = _norm(t)
        if not n or n in seen:
            continue
        words = {w for w in (_norm(w) for w in re.split(r"[^A-Za-z0-9]+", t or "")) if len(w) >= 3}
        if n in compare or (words & compare):
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

    Selection is COLLECTION-FIRST: if the post theme names a piece's ``collection``
    (e.g. theme='4th-of-july'), that piece wins outright over any piece whose collection
    the theme did not name — a themed draft always draws from the themed collection, not
    an incidentally style-heavy piece from another one. Within that primary bucket (and
    when no collection is named at all), the tag score decides:
    Score = 2·(style tags matching the artist's style OR the theme) + 1·(motif tags
    matching the theme) + 1 if flagged the artist's best example. Deterministic
    tie-break: collection-match, then higher score, then best-example, then CSV (real
    artwork) ahead of seed (mock), then a stable asset-id order — so the same inputs
    always pick the same piece (needed for exactly-once staging).

    If the top piece has NO tag/collection overlap at all it is still returned, but as an
    honest PORTFOLIO FALLBACK (``exact_match=False``) — the "why" says so rather than
    claiming a match that is not there."""
    if not artworks:
        return None

    # The theme side is expanded with in-domain synonyms so the operator's marketing
    # words reach the library's descriptive tags ("botanical" -> "Dahlia flower").
    theme_cmp = _expand_theme(_norm_set(theme_terms))
    style_cmp = _norm_set(artist_styles) | theme_cmp

    scored: list[
        tuple[int, int, int, int, bool, int, str, ArtworkRef, list[str], list[str], str]
    ] = []
    for a in artworks:
        matched_styles = _overlap(a.styles, style_cmp)
        matched_motifs = _overlap(a.motifs, theme_cmp)
        # The theme may name the piece's collection directly (e.g. theme='4th-of-july').
        matched_collection = a.collection if (_norm(a.collection) and _norm(a.collection) in theme_cmp) else ""
        coll_flag = 1 if matched_collection else 0
        score = 2 * len(matched_styles) + len(matched_motifs) + (1 if a.is_best_example else 0)
        # THEME RELEVANCE OUTRANKS TAG COUNT.
        # ``style_cmp`` unions the ARTIST'S OWN styles with the theme, so every piece
        # matches its own style tags and ``score`` degenerates into a count of how heavily
        # a piece is tagged. The busiest piece therefore won whatever the brief said: a
        # "fine-line botanical" campaign surfaced Spider-Man masks, a dragon and Jack
        # Skellington, because those carry six style tags and the botanical pieces carry
        # two. Every piece real, every "why" honest — and every one of them wrong, which a
        # client sees at a glance. So how well a piece matches what the OPERATOR ASKED FOR
        # is a primary sort key ABOVE raw tag score; score only breaks ties within an equal
        # level of theme relevance.
        #
        # It measures HOW MUCH OF THE BRIEF a piece answers — the number of DISTINCT brief
        # terms it covers — not how many of its tags happen to match. Counting matched TAGS
        # rewards verbosity all over again: under "dragon blackwork" the Spider-Man video
        # carries six tags containing the word blackwork and scored 6, while the actual
        # dragon piece — which answers BOTH halves of the brief, dragon AND blackwork —
        # scored 2 and lost. A piece that satisfies two of the operator's words is a better
        # answer than a piece that satisfies one of them six times over.
        # With no theme terms this is 0 for every piece and the old ordering is unchanged.
        theme_hits = _brief_coverage(a, theme_terms)
        if matched_collection:
            theme_hits += 1
        # DEPTH breaks a coverage tie, before generic tag score gets a vote.
        # Coverage alone cannot separate two pieces that answer the same brief words:
        # "botanical" and "floral" share one synonym set, so a piece carrying a single rose
        # covers exactly what a piece carrying dahlias, sunflowers, wildflowers and foliage
        # covers. On that tie the generic tag score took over and the one-rose piece won a
        # botanical brief. Depth counts how MANY of the piece's own tags are on-theme, so
        # the piece that is more thoroughly about the brief leads.
        theme_depth = len(_overlap(a.styles, theme_cmp)) + len(matched_motifs)
        # CSV (real, first-party artwork) outranks seed (mock) on an otherwise exact tie.
        source_rank = 0 if _norm(a.source) == _norm("csv") else 1
        scored.append(
            (coll_flag, theme_hits, theme_depth, score, a.is_best_example, source_rank, a.asset_id, a, matched_styles, matched_motifs, matched_collection)
        )

    # Best: collection-match, then BRIEF COVERAGE (how many of the operator's words this
    # piece answers), then ON-THEME DEPTH (how thoroughly it answers them), then the
    # generic tag score, then best-example, CSV-over-seed, and a stable asset-id — fully
    # deterministic, and relevance to the brief always outranks how heavily a piece is
    # tagged.
    scored.sort(key=lambda t: (-t[0], -t[1], -t[2], -t[3], not t[4], t[5], t[6]))
    _cf, _th, _td, score, _best, _src, _aid, art, matched_styles, matched_motifs, matched_collection = scored[0]
    exact = bool(matched_styles or matched_motifs or matched_collection)
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
        styles=list(art.styles),
        motifs=list(art.motifs),
        collection=art.collection,
        matched_collection=matched_collection,
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
                f"tagged {_join(pick.matched_styles)}, matching {artist}'s style"
            )
        if pick.matched_motifs:
            bits.append(f"its {_join(pick.matched_motifs)} motif fits this post")
        if pick.matched_collection:
            bits.append(f"it is part of the {pick.matched_collection} collection")
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
