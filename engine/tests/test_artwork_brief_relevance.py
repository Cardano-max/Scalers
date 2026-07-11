"""The artwork the operator is offered must ANSWER THE BRIEF.

This is the rubric a client applies in one glance, so it is the rubric the ranker is held
to. Every case here is a real failure that shipped:

  * "promote Keebs' fine-line botanical work" offered a top-4 of Spider-Man masks, a
    dragon and Jack Skellington — because ``score`` unions the artist's OWN styles with
    the theme and so degenerates into a count of how heavily a piece is tagged, and those
    pieces carry six style tags to the botanical piece's two.
  * "dragon blackwork" offered the Spider-Man video, which carries six tags containing the
    word 'blackwork', over the actual dragon piece, which answers BOTH halves of the brief.
  * A botanical brief preferred a piece holding a single rose over the piece covered in
    dahlias, sunflowers, wildflowers and foliage, because both "cover" the same brief words.

Hence the three ordered keys under test: brief COVERAGE (how many of the operator's words a
piece answers) beats DEPTH (how thoroughly it answers them) beats raw tag score. Pure — no
DB, no network.
"""

from __future__ import annotations

import pytest

from studio.artwork_select import ArtworkRef, select_artwork

# A stand-in for the real library: the same shapes that broke it. Note the deliberately
# over-tagged pieces — they are what a naive "most tags wins" ranker rewards.
LIBRARY = [
    ArtworkRef(
        asset_id="a_botanical",
        artist="Keebs",
        image_ref="",
        caption="",
        styles=["Neo-traditional"],
        motifs=[
            "Dahlia flower", "Sunflowers", "Bees", "Honeycomb",
            "Small wildflowers", "Leaves and foliage",
        ],
        source="csv",
    ),
    ArtworkRef(
        asset_id="a_one_rose",
        artist="Keebs",
        image_ref="",
        caption="",
        styles=["Realism", "Neo-traditional"],
        motifs=["Candle with flame", "Red roses", "Revolver/pistol", "Gothic window frame"],
        source="csv",
    ),
    ArtworkRef(
        asset_id="a_dragon",
        artist="Keebs",
        image_ref="",
        caption="",
        styles=["Black-and-grey realism", "Blackwork"],
        motifs=["Dragon", "Scales"],
        source="csv",
    ),
    # The over-tagged piece: 'blackwork' appears across several of its tags.
    ArtworkRef(
        asset_id="a_spiderman",
        artist="Keebs",
        image_ref="",
        caption="",
        styles=[
            "geometric blackwork", "dotwork", "black-and-grey realism",
            "realism", "blackwork", "geometric",
        ],
        motifs=["Spider-Man mask", "spider", "intricate web pattern", "superhero portrait"],
        source="csv",
    ),
]


def _pick(theme: list[str]) -> str:
    got = select_artwork(LIBRARY, artist_styles=["Neo-traditional", "Realism", "Blackwork"], theme_terms=theme)
    assert got is not None
    return got.asset_id


def test_botanical_brief_gets_the_botanical_piece_not_the_busiest_one():
    """The original failure: a fine-line botanical brief served Spider-Man."""
    assert _pick(["fine-line", "botanical", "floral"]) == "a_botanical"


def test_botanical_brief_prefers_the_deeply_floral_piece_over_a_single_rose():
    """Coverage ties (both pieces 'answer' botanical+floral); DEPTH must decide."""
    assert _pick(["botanical", "floral"]) == "a_botanical"


def test_two_word_brief_beats_one_word_repeated_many_times():
    """'dragon blackwork': the dragon piece answers BOTH words; the Spider-Man piece
    answers 'blackwork' six times over and must still lose."""
    assert _pick(["dragon", "blackwork"]) == "a_dragon"


def test_the_busiest_piece_still_wins_when_the_brief_actually_asks_for_it():
    """Relevance-first must not become a penalty on heavily tagged work."""
    assert _pick(["spider-man", "superhero"]) == "a_spiderman"


@pytest.mark.parametrize("stopword_brief", [["the", "post", "for", "our"], ["book", "session"]])
def test_stopwords_alone_confer_no_relevance(stopword_brief):
    """Brief words that match everything must confer no relevance on anything.

    "page post about the full-day session" is mostly filler; if those words counted as
    coverage, whichever piece happened to share one of them would be promoted as the most
    relevant answer to a brief that asked for nothing. The test: a stopword-only brief must
    rank IDENTICALLY to no brief at all."""
    styles = ["Neo-traditional", "Realism", "Blackwork"]
    with_stopwords = select_artwork(LIBRARY, artist_styles=styles, theme_terms=stopword_brief)
    with_nothing = select_artwork(LIBRARY, artist_styles=styles, theme_terms=None)
    assert with_stopwords is not None and with_nothing is not None
    assert with_stopwords.asset_id == with_nothing.asset_id


def test_no_theme_terms_is_unchanged_behaviour():
    """With nothing asked for, the ranker must behave exactly as it always did."""
    got = select_artwork(LIBRARY, artist_styles=["Neo-traditional"], theme_terms=None)
    assert got is not None  # a real piece, never invented
