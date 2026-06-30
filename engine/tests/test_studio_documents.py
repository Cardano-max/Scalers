"""Persistent tenant document store — pure/offline tests (no DB, no network).

Covers the chunker + summary (pure functions) and the HEADLINE behaviour: the host's
per-turn context (``build_documents_context``) lists an ACTIVE document and injects a
retrieved passage, and a deactivated doc (absent from the active index) is NOT shown.
The store is monkeypatched here so the logic is proven without Postgres; the real
round-trip lives in ``test_studio_documents_pg.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

from studio import documents as docstore
from studio.agui import CampaignPlan, build_documents_context


# --- pure chunker + summary ------------------------------------------------- #
def test_chunk_document_tracks_headings_and_splits() -> None:
    doc = (
        "# Brand Voice\n\nWe write warm and direct, never girlboss hype.\n\n"
        "## Guardrails\n\nNo banned emoji. No unapproved discount codes.\n"
    )
    chunks = docstore.chunk_document(doc)
    headings = [h for h, _ in chunks]
    assert "Brand Voice" in headings and "Guardrails" in headings
    # heading lines themselves are not emitted as passage bodies
    assert all(not t.lstrip().startswith("#") for _, t in chunks)


def test_chunk_document_empty_is_empty() -> None:
    assert docstore.chunk_document("") == []
    assert docstore.chunk_document("   \n\n  ") == []


def test_summarize_takes_first_substantive_paragraph() -> None:
    s = docstore.summarize("# Title\n\nLadies First is woman-owned Austin color.\n")
    assert s == "Ladies First is woman-owned Austin color."


# --- headline: host context lists active docs, excludes removed ones -------- #
_ACTIVE = [
    {
        "id": "doc_1",
        "name": "Ladies First Brand & Campaign Playbook",
        "kind": "brand",
        "summary": "Woman-owned Austin appointment-only color studio.",
        "chars": 26000,
        "chunks": 28,
    }
]
_PASSAGE = [
    {
        "document_id": "doc_1",
        "doc_name": "Ladies First Brand & Campaign Playbook",
        "kind": "brand",
        "heading": "Brand identity & voice",
        "content": "Warm, direct, never girlboss hype; neo-traditional color.",
        "seq": 3,
        "rank": 0.2,
    }
]


def test_host_context_lists_active_doc_and_injects_passage(monkeypatch) -> None:
    monkeypatch.setattr(docstore, "active_docs_index", lambda tid, dsn=None: _ACTIVE)
    monkeypatch.setattr(docstore, "retrieve", lambda tid, q, k=4, dsn=None: _PASSAGE)
    plan = CampaignPlan(goal="win back lapsed clients", audience="past clients")
    out = build_documents_context("ladies8391", plan, None)
    assert "TENANT DOCUMENT STORE" in out
    assert "Ladies First Brand & Campaign Playbook" in out  # listed by name
    assert "RELEVANT PASSAGES" in out
    assert "Brand identity & voice" in out  # cited section
    assert "neo-traditional color" in out  # real passage content


def test_host_context_honest_empty_when_no_active_docs(monkeypatch) -> None:
    # A deactivated/removed doc is simply absent from the active index → the host is
    # told honestly that NO documents are uploaded (never fabricate one).
    monkeypatch.setattr(docstore, "active_docs_index", lambda tid, dsn=None: [])
    monkeypatch.setattr(docstore, "retrieve", lambda tid, q, k=4, dsn=None: [])
    out = build_documents_context("ladies8391", CampaignPlan(goal="g"), None)
    assert "NO uploaded documents" in out
    assert "Ladies First" not in out  # nothing invented
    assert "NEVER claim to have a document you do not." in out
