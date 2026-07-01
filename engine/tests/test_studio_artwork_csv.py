"""Unit tests for the CSV artwork source adapter (Rec 3).

DB-free: the CSV parsing, normalization, deterministic ids, and content shape are PURE.
The real-Postgres library round-trip (:func:`seed_artwork_from_csv`) is exercised by the
live proof script, not here (it needs a real store).
"""

from __future__ import annotations

import os

from studio.adapters.artwork_source import (
    ARTWORK_SOURCE_CSV,
    CatalogArtwork,
    CsvArtworkSource,
    _split_tags,
    _truthy,
)
from studio.artwork_select import ARTWORK_ASSET_TYPE, ArtworkRef

_HEADER = "artist,image_ref,caption,styles,motifs,collection,is_best_example"
_ROW = (
    "Ladies First Flash,flash://ladies8391/pride/IMG_1735.PNG,Pride flash design,"
    "traditional;illustrative,pride;rainbow,pride,false"
)


# --------------------------------------------------------------------------- #
# Tolerant parsing
# --------------------------------------------------------------------------- #
def test_parses_a_well_formed_row():
    arts = list(CsvArtworkSource(f"{_HEADER}\n{_ROW}").artworks())
    assert len(arts) == 1
    a = arts[0]
    assert a.artist == "Ladies First Flash"
    assert a.image_ref == "flash://ladies8391/pride/IMG_1735.PNG"
    assert a.styles == ["traditional", "illustrative"]
    assert a.motifs == ["pride", "rainbow"]
    assert a.collection == "pride"
    assert a.is_best_example is False


def test_empty_or_header_only_csv_yields_zero_rows():
    assert list(CsvArtworkSource("").artworks()) == []
    assert list(CsvArtworkSource(_HEADER).artworks()) == []


def test_rows_missing_artist_or_image_ref_are_skipped_not_fabricated():
    rows = "\n".join([
        _HEADER,
        ",flash://ladies8391/flash/x.png,cap,traditional,flash-sheet,flash,false",  # no artist
        "Ladies First Flash,,cap,traditional,flash-sheet,flash,false",              # no image_ref
        _ROW,                                                                        # valid
    ])
    arts = list(CsvArtworkSource(rows).artworks())
    assert len(arts) == 1 and arts[0].image_ref.endswith("IMG_1735.PNG")


def test_malformed_tag_cells_never_crash_and_degrade_to_empty():
    row = "Ladies First Flash,flash://ladies8391/flash/x.png,cap,;;,,flash,false"
    arts = list(CsvArtworkSource(f"{_HEADER}\n{row}").artworks())
    assert len(arts) == 1
    assert arts[0].styles == [] and arts[0].motifs == []


def test_split_tags_handles_comma_or_semicolon_and_blanks():
    assert _split_tags("a; b ;;c") == ["a", "b", "c"]
    assert _split_tags("a,b") == ["a", "b"]
    assert _split_tags("") == [] and _split_tags(None) == []


def test_truthy_only_accepts_affirmative_tokens():
    assert _truthy("true") and _truthy("YES") and _truthy("1")
    assert not _truthy("false") and not _truthy("") and not _truthy("no")


# --------------------------------------------------------------------------- #
# Deterministic ids + honest provenance
# --------------------------------------------------------------------------- #
def test_asset_id_is_deterministic_and_csv_namespaced():
    a = CatalogArtwork("Ladies First Flash", "flash://ladies8391/pride/IMG_1735.PNG")
    aid = a.asset_id("ladies8391")
    assert aid == a.asset_id("ladies8391")  # stable
    assert aid.startswith("art_csv_ladies8391_")
    # A different image_ref yields a different id (no collisions across pieces).
    b = CatalogArtwork("Ladies First Flash", "flash://ladies8391/pride/IMG_1730.PNG")
    assert b.asset_id("ladies8391") != aid


def test_content_marks_csv_source_and_round_trips_through_artwork_ref():
    a = list(CsvArtworkSource(f"{_HEADER}\n{_ROW}").artworks())[0]
    content = a.content()
    assert content["source"] == ARTWORK_SOURCE_CSV
    assert content["collection"] == "pride"
    # It reads back through the SAME normalizer the drafter uses.
    ref = ArtworkRef.from_asset_row(
        {"id": a.asset_id("ladies8391"), "asset_type": ARTWORK_ASSET_TYPE, "content": content}
    )
    assert ref is not None
    assert ref.source == "csv" and ref.collection == "pride"
    assert ref.styles == ["traditional", "illustrative"]


# --------------------------------------------------------------------------- #
# The committed real catalog is well-formed (all 105 flash images).
# --------------------------------------------------------------------------- #
def _catalog_path() -> str:
    here = os.path.dirname(__file__)
    return os.path.normpath(os.path.join(here, "..", "studio", "data", "flash_tattoos_catalog.csv"))


def test_committed_flash_catalog_is_complete_and_unique():
    arts = list(CsvArtworkSource.from_path(_catalog_path()).artworks())
    assert len(arts) == 105  # every real Flash Tattoos image
    refs = [a.image_ref for a in arts]
    assert len(set(refs)) == 105  # no duplicate refs -> no id collisions on ingest
    # Every row is honestly first-party attributed and collection-tagged.
    assert all(a.artist == "Ladies First Flash" for a in arts)
    assert all(a.collection for a in arts)
    assert all(a.image_ref.startswith("flash://ladies8391/") for a in arts)
    # No fabricated best-example flags (kept false unless a real reason).
    assert not any(a.is_best_example for a in arts)


def test_committed_catalog_covers_the_expected_collections():
    arts = list(CsvArtworkSource.from_path(_catalog_path()).artworks())
    by_collection: dict[str, int] = {}
    for a in arts:
        by_collection[a.collection] = by_collection.get(a.collection, 0) + 1
    assert by_collection == {
        "4th-of-july": 19,
        "lunch-menu": 34,
        "build-a-pin": 23,
        "pride": 9,
        "flash": 20,
    }
