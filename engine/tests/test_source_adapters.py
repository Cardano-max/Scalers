"""Future-ready source adapters (P1 #4) — CSV impls work now; stubs are honest.

Pins the ADR §4.1 contract: nothing is hardcoded to one CSV (swapping to Stribe /
Mini-App is a config change), and a not-connected source raises a clear
``NotConfiguredError`` rather than fabricating a lead / thread / artist.

All pure — no DB (the DbConversationSource is covered separately).
"""

from __future__ import annotations

import pytest

from studio.adapters import NotConfiguredError
from studio.adapters.artist_source import (
    Artist,
    CsvArtistSource,
    FutureMiniAppArtistApi,
    SeededArtistSource,
)
from studio.adapters.lead_source import (
    CsvLeadSource,
    Lead,
    LeadSourceProtocol,
    MiniAppCrmSource,
    StribeConversationSource,
)
from studio.adapters.message_source import (
    MessageSourceProtocol,
    StribeSmsThread,
    UploadedConversationFile,
)


def test_csv_lead_source_normalizes_extended_fields():
    csv_text = (
        "name,email,location,styles,artist,customer_type,notes\n"
        "Sarah,sarah@x.com,\"Austin, TX\",fine-line; floral,Maya,artist_specific,short on budget\n"
    )
    leads = list(CsvLeadSource(csv_text).leads())
    assert len(leads) == 1
    lead = leads[0]
    assert lead.name == "Sarah" and lead.email == "sarah@x.com"
    assert lead.city == "Austin" and lead.state == "TX"
    assert lead.interests == ["fine-line", "floral"]
    assert lead.artist == "Maya" and lead.customer_type == "artist_specific"
    assert lead.notes == "short on budget"
    # It satisfies the LeadSourceProtocol structurally.
    assert isinstance(CsvLeadSource(csv_text), LeadSourceProtocol)
    # as_upsert_row carries the real values the DB upsert accepts (notes included).
    row = lead.as_upsert_row()
    assert row["notes"] == "short on budget" and row["artist"] == "Maya"


def test_csv_lead_source_carries_unknown_columns_never_drops():
    leads = list(CsvLeadSource("name,favorite_color\nRae,teal\n").leads())
    assert leads[0].extra == {"favorite_color": "teal"}


def test_lead_stubs_raise_honest_not_connected():
    with pytest.raises(NotConfiguredError, match="Stribe is not connected"):
        list(StribeConversationSource().leads())
    with pytest.raises(NotConfiguredError, match="Mini-App CRM is not connected"):
        list(MiniAppCrmSource().leads())


def test_uploaded_conversation_file_parses_and_stub_raises():
    src = UploadedConversationFile(
        "Customer: I love the floral flash / Studio: want to book?", customer_id="c1"
    )
    thread = src.thread_for("c1")
    assert thread is not None and thread.has_turns
    assert thread.turns[0]["speaker"] == "customer"
    assert isinstance(src, MessageSourceProtocol)
    # Empty / unparseable -> honest None, never a fabricated thread.
    assert UploadedConversationFile("no speakers here").thread_for("c1") is None
    with pytest.raises(NotConfiguredError, match="Stribe SMS"):
        StribeSmsThread().thread_for("c1")


def test_artist_sources_work_now_and_stub_raises():
    seeded = SeededArtistSource([Artist(name="Maya", styles=["fine-line"])])
    assert seeded.names() == ["Maya"]
    csv_artists = list(CsvArtistSource("name,styles\nMaya,fine-line; floral\n").artists())
    assert csv_artists[0].name == "Maya" and "floral" in csv_artists[0].styles
    with pytest.raises(NotConfiguredError, match="Mini-App artist API"):
        list(FutureMiniAppArtistApi().artists())
