"""Multimodal ingestion — grounded, cited extraction + honest degradation.

Proves the no-fabrication gate: a fact is emitted ONLY when a real citation span
overlaps its line; an uncited claim is dropped; unsupported formats and a missing
key degrade to :class:`NotConfiguredError` with ZERO facts (never fabricated).

The pure core (:func:`facts_from_blocks`) is exercised against synthetic Anthropic
response blocks (no network). A live end-to-end call is gated behind
``SCALERS_RUN_LIVE_INGEST=1`` so it never touches the ambient suite.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import harness.config  # noqa: F401 — loads engine/.env so the gated live test sees the key
from studio import ingest_vlm
from studio.ingest_vlm import (
    NotConfiguredError,
    facts_from_blocks,
    facts_from_image_blocks,
    guess_media_type,
    ingest_bytes,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PLAYBOOK = FIXTURES / "ladies_first_brand_playbook.md"


# --------------------------------------------------------------------------- #
# Synthetic Anthropic response blocks (duck-typed like the SDK's pydantic objects).
# --------------------------------------------------------------------------- #
def _text(text: str, citations: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text, citations=citations)


def _char(cited_text: str, start: int, end: int, title: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        type="char_location",
        cited_text=cited_text,
        start_char_index=start,
        end_char_index=end,
        document_title=title,
    )


def _page(cited_text: str, start: int, end: int, title: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        type="page_location",
        cited_text=cited_text,
        start_page_number=start,
        end_page_number=end,
        document_title=title,
    )


# --------------------------------------------------------------------------- #
# Pure core — char-location (text/markdown source).
# --------------------------------------------------------------------------- #
def test_char_location_fact_is_grounded_with_span():
    blocks = [
        _text(
            "[brand_voice] The voice is warm and encouraging.\n",
            [_char("warm, encouraging, unhurried tone", 40, 73, "playbook.md")],
        )
    ]
    facts, dropped = facts_from_blocks(blocks, source_doc_id="vlmdoc_t_pb")
    assert dropped == 0
    assert len(facts) == 1
    f = facts[0]
    assert f.field == "brand_voice"
    assert "warm and encouraging" in f.value
    assert f.signal == "cited"
    assert f.citation.kind == "char"
    assert f.citation.locus == "chars 40-73"
    assert f.citation.cited_text == "warm, encouraging, unhurried tone"
    assert f.citation.source_doc_id == "vlmdoc_t_pb"
    assert f.citation.document_title == "playbook.md"


def test_tag_and_cited_span_may_be_split_across_blocks():
    # The API often splits a line into an uncited tag block + a cited span block.
    # Both are on the SAME line, so the fact is still grounded (tag carries over).
    blocks = [
        _text("[audience] ", None),
        _text("Clients are women aged 24 to 45.\n", [_char("women aged 24 to 45", 90, 109)]),
    ]
    facts, dropped = facts_from_blocks(blocks, source_doc_id="doc")
    assert dropped == 0
    assert [f.field for f in facts] == ["audience"]
    assert facts[0].citation.locus == "chars 90-109"
    assert "women aged 24 to 45" in facts[0].value


def test_uncited_claim_is_dropped_never_fabricated():
    # A claim the model states WITHOUT a citation is dropped and counted.
    blocks = [
        _text("[claim] We are the best studio in the whole city.\n", None),
        _text("[offer] One free touch-up within six months.\n", [_char("free touch-up", 200, 213)]),
    ]
    facts, dropped = facts_from_blocks(blocks, source_doc_id="doc")
    assert dropped == 1  # the uncited [claim] line
    assert [f.field for f in facts] == ["offer"]  # only the cited fact survives


def test_none_marker_yields_no_facts_and_no_drops():
    facts, dropped = facts_from_blocks([_text("NONE", None)], source_doc_id="doc")
    assert facts == [] and dropped == 0


def test_untagged_cited_line_defaults_to_fact_field():
    blocks = [_text("Single-needle botanical linework.\n", [_char("single-needle", 5, 18)])]
    facts, _ = facts_from_blocks(blocks, source_doc_id="doc")
    assert len(facts) == 1 and facts[0].field == "fact"


# --------------------------------------------------------------------------- #
# Pure core — page-location (PDF source).
# --------------------------------------------------------------------------- #
def test_page_location_single_page_locus():
    blocks = [_text("[service] Fine-line botanical work.\n", [_page("botanical", 3, 4)])]
    facts, _ = facts_from_blocks(blocks, source_doc_id="doc")
    assert facts[0].citation.kind == "page"
    assert facts[0].citation.locus == "p.3"  # end is exclusive -> single page 3


def test_page_location_multi_page_locus():
    blocks = [_text("[positioning] Premium fine-line studio.\n", [_page("premium", 2, 4)])]
    facts, _ = facts_from_blocks(blocks, source_doc_id="doc")
    assert facts[0].citation.locus == "pp.2-3"


# --------------------------------------------------------------------------- #
# Image path — image-level locus, marked weaker.
# --------------------------------------------------------------------------- #
def test_image_facts_carry_image_level_locus():
    blocks = [_text("[visual_style] Fine-line floral blackwork with negative space.\n")]
    facts = facts_from_image_blocks(blocks, source_doc_id="vlmdoc_t_art")
    assert len(facts) == 1
    assert facts[0].signal == "image_visual"
    assert facts[0].citation.kind == "image"
    assert facts[0].citation.locus == "image:vlmdoc_t_art"


# --------------------------------------------------------------------------- #
# Format detection + honest degradation.
# --------------------------------------------------------------------------- #
def test_media_type_detection():
    assert guess_media_type("a.pdf") == "application/pdf"
    assert guess_media_type("a.md") == "text/plain"
    assert guess_media_type("a.PNG") == "image/png"
    assert guess_media_type("a.docx") is None  # convert-first family
    assert guess_media_type("a.bin") is None


def test_unsupported_office_format_degrades_honestly():
    with pytest.raises(NotConfiguredError) as ei:
        ingest_bytes("ladies8391", "artist_deck.pptx", b"PK\x03\x04fake")
    assert "PowerPoint" in str(ei.value)
    with pytest.raises(NotConfiguredError) as ei2:
        ingest_bytes("ladies8391", "sheet.xlsx", b"PK\x03\x04fake")
    assert "Excel" in str(ei2.value)


def test_missing_key_degrades_before_any_network(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ingest_vlm.is_configured() is False
    with pytest.raises(NotConfiguredError) as ei:
        ingest_bytes("ladies8391", "playbook.md", b"# some real text here")
    assert "ANTHROPIC_API_KEY" in str(ei.value)


def test_missing_tenant_is_rejected():
    with pytest.raises(ValueError):
        ingest_bytes("", "playbook.md", b"# text")


# --------------------------------------------------------------------------- #
# REAL end-to-end call — gated so it never runs in the ambient suite.
#   Run with:  SCALERS_RUN_LIVE_INGEST=1 uv run pytest tests/test_ingest_vlm.py -k live -q
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not (os.environ.get("SCALERS_RUN_LIVE_INGEST") and ingest_vlm.is_configured()),
    reason="live ingest disabled (set SCALERS_RUN_LIVE_INGEST=1 with a real key)",
)
def test_live_ingest_real_playbook_has_traceable_citations():
    source = PLAYBOOK.read_text(encoding="utf-8")
    result = ingest_vlm.ingest_file("ladies8391", PLAYBOOK)

    assert result.facts, "expected at least one grounded fact from the real playbook"
    for f in result.facts:
        assert f.signal == "cited"
        assert f.citation.kind == "char"
        assert f.citation.cited_text, "every fact must carry a real cited span"
        # The cited span must actually be present in the source bytes (real grounding).
        assert f.citation.cited_text in source
        # And the char locus must point at that span.
        start, end = (int(x) for x in f.citation.locus.removeprefix("chars ").split("-"))
        assert source[start:end] == f.citation.cited_text
