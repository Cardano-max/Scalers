"""Unit tests for the studio artwork LIBRARY + evidence-grounded selection (P2).

DB-free: the selection + "why" + normalization are PURE, so these run in the standard
unit sweep. The real-Postgres seed round-trip is covered by
``test_studio_post_campaign_pg.py`` (gated on ENGINE_DATABASE_URL).
"""

from __future__ import annotations

from studio.artwork_select import (
    ARTWORK_ASSET_TYPE,
    ArtworkRef,
    SEED_ARTWORK,
    _norm,
    _seed_asset_id,
    artist_styles,
    select_artwork,
)


def _art(asset_id, artist, caption, styles, motifs, best=False):
    return ArtworkRef(asset_id, artist, f"ref://{asset_id}", caption, styles, motifs, best)


# --------------------------------------------------------------------------- #
# Normalization / matching
# --------------------------------------------------------------------------- #
def test_norm_collapses_separators_so_fine_line_variants_match():
    assert _norm("fine-line") == _norm("fine line") == _norm("FineLine") == "fineline"


def test_from_asset_row_ignores_non_artwork_rows():
    assert ArtworkRef.from_asset_row({"asset_type": "post", "content": {}}) is None


def test_from_asset_row_excludes_broll_video():
    """A b-roll VIDEO shares the studio_artwork asset type but must never surface in
    the 'pick 1 of 4 IMAGES' picker — it is chosen separately via load_broll."""
    row = {
        "id": "vid1",
        "asset_type": ARTWORK_ASSET_TYPE,
        "content": {"artist": "Keebs", "media": "video", "motifs": ["Spider-Man"]},
    }
    assert ArtworkRef.from_asset_row(row) is None
    # An image row of the same type is still valid.
    img = {"id": "img1", "asset_type": ARTWORK_ASSET_TYPE, "content": {"artist": "Keebs", "media": "image"}}
    assert ArtworkRef.from_asset_row(img) is not None


def test_from_asset_row_never_fabricates_missing_fields():
    ref = ArtworkRef.from_asset_row(
        {"id": "x1", "asset_type": ARTWORK_ASSET_TYPE, "content": {"artist": "Maya"}}
    )
    assert ref is not None
    assert ref.caption == "" and ref.styles == [] and ref.motifs == []


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #
def test_empty_library_returns_none_not_a_fabricated_pick():
    assert select_artwork([], artist_styles=["fine-line"], theme_terms=["floral"]) is None


def test_style_and_motif_overlap_drive_the_pick_and_matches():
    arts = [
        _art("a1", "Maya", "Fine-line peony", ["fine-line", "floral"], ["peony"], best=True),
        _art("a2", "Maya", "Minimal wave", ["fine-line", "minimalist"], ["wave"]),
    ]
    pick = select_artwork(arts, artist_styles=["fine-line", "floral"], theme_terms=["floral", "peony"])
    assert pick is not None
    assert pick.asset_id == "a1"
    assert pick.exact_match is True
    assert set(pick.matched_styles) == {"fine-line", "floral"}
    assert pick.matched_motifs == ["peony"]


def test_tie_break_is_deterministic_by_asset_id():
    # Two identical-scoring pieces -> stable choice (ascending asset id) both orderings.
    a = _art("a_z", "Rae", "Rose", ["neo-traditional"], ["rose"])
    b = _art("a_a", "Rae", "Bloom", ["neo-traditional"], ["bloom"])
    p1 = select_artwork([a, b], artist_styles=["neo-traditional"], theme_terms=[])
    p2 = select_artwork([b, a], artist_styles=["neo-traditional"], theme_terms=[])
    assert p1.asset_id == p2.asset_id == "a_a"


def test_no_overlap_falls_back_honestly_not_a_claimed_match():
    arts = [_art("b1", "Noor", "Geometric blackwork", ["blackwork"], ["geometric"])]
    pick = select_artwork(arts, artist_styles=[], theme_terms=["floral"])
    assert pick is not None
    assert pick.exact_match is False
    assert pick.matched_styles == [] and pick.matched_motifs == []
    assert "no match was claimed" in pick.why.lower()


# --------------------------------------------------------------------------- #
# The "why" is fully grounded — every concrete token traces to a stored field.
# --------------------------------------------------------------------------- #
def test_why_only_references_real_stored_fields():
    art = _art("a1", "Maya", "Fine-line peony on the forearm", ["fine-line", "floral"], ["peony"], best=True)
    pick = select_artwork([art], artist_styles=["fine-line"], theme_terms=["peony"])
    why = pick.why
    # Caption, asset id, artist and every matched tag must literally appear in the why.
    assert art.caption in why
    assert art.asset_id in why
    assert "Maya" in why
    for tag in pick.matched_styles + pick.matched_motifs:
        assert tag in why


def test_why_has_no_em_dash_ai_tell():
    art = _art("a1", "Maya", "Fine-line peony", ["fine-line"], ["peony"], best=True)
    pick = select_artwork([art], artist_styles=["fine-line"], theme_terms=["peony"])
    assert "—" not in pick.why


def test_artist_styles_derived_from_own_portfolio_deduped():
    arts = [
        _art("a1", "Maya", "peony", ["fine-line", "floral"], []),
        _art("a2", "Maya", "wave", ["Fine-Line", "minimalist"], []),  # dup by canon token
    ]
    styles = artist_styles(arts)
    assert styles == ["fine-line", "floral", "minimalist"]


# --------------------------------------------------------------------------- #
# Seed shape / determinism (no DB)
# --------------------------------------------------------------------------- #
def test_seed_asset_id_is_deterministic():
    assert _seed_asset_id("ladies8391", "Maya", 0) == "art_ladies8391_maya_00"
    assert _seed_asset_id("ladies8391", "Maya", 0) == _seed_asset_id("ladies8391", "Maya", 0)


def test_seed_portfolio_has_tagged_pieces_for_each_artist():
    assert set(SEED_ARTWORK) == {"Maya", "Rae", "Noor"}
    for pieces in SEED_ARTWORK.values():
        assert pieces
        for p in pieces:
            assert p["caption"] and p["styles"]  # every seed piece carries real metadata
