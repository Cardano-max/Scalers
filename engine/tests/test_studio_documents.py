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


# --- CSV-aware chunking (one passage per row) ------------------------------- #
_CSV = (
    "name,email,city,notes\n"
    "Monolith Tattoo,appointments@monolith.com,Austin,asked about cover-ups\n"
    "Ink & Co,hello@inkco.com,Dallas,wants a consult\n"
    "Sailor Jane,jane@sailorjane.com,Houston,\n"
)


def test_chunk_csv_one_passage_per_row() -> None:
    # kind='csv' routes to per-row chunking: one passage per DATA row (header excluded).
    chunks = docstore.chunk_document(_CSV, kind="csv")
    assert len(chunks) == 3  # three data rows, header not a chunk
    headings = [h for h, _ in chunks]
    assert headings == ["Row 1", "Row 2", "Row 3"]
    # Each passage carries the header context so a cell is retrievable in isolation.
    first = chunks[0][1]
    assert first.startswith("Row 1 — ")
    assert "name: Monolith Tattoo" in first
    assert "email: appointments@monolith.com" in first
    assert "notes: asked about cover-ups" in first
    # Empty cells are dropped, not emitted as blanks.
    assert "notes:" not in chunks[2][1]


def test_chunk_csv_autodetect_without_kind() -> None:
    # No explicit kind: a real delimited table sniffs as CSV and chunks per row.
    chunks = docstore.chunk_document(_CSV)
    assert len(chunks) == 3
    assert chunks[1][1].startswith("Row 2 — ")


def test_summarize_csv_reports_rows_and_columns() -> None:
    s = docstore.summarize(_CSV, kind="csv")
    assert s == "CSV: 3 rows; columns: name, email, city, notes"


def test_summarize_csv_singular_one_row() -> None:
    s = docstore.summarize("a,b\n1,2\n", kind="csv")
    assert s == "CSV: 1 row; columns: a, b"


def test_markdown_doc_not_misrouted_as_csv() -> None:
    # Prose with a comma must stay prose (markdown path), never CSV per-row chunking.
    doc = "# Brand Voice\n\nWe write warm, direct copy, never girlboss hype.\n"
    chunks = docstore.chunk_document(doc)
    assert [h for h, _ in chunks] == ["Brand Voice"]
    assert not any(t.startswith("Row ") for _, t in chunks)


def test_autodetect_is_conservative_for_comma_prose() -> None:
    # No-kind fallback must be STRICT: comma-bearing prose (no consistent table shape)
    # stays a doc. A brand playbook must never be row-chunked.
    assert docstore._looks_like_csv("# Title\n\nWe are warm, direct, kind.\n") is False
    # Two prose lines that happen to share a comma count are NOT a table (no header
    # semantics) — but a single comma line definitely is not.
    assert docstore._looks_like_csv("Ladies First is woman-owned, Austin-based.\n") is False
    # A real header + multiple consistent rows DOES sniff as CSV.
    assert docstore._looks_like_csv("name,email\nA,a@x.com\nB,b@x.com\n") is True


def test_brand_playbook_kind_is_not_row_chunked() -> None:
    # An explicit non-csv kind always uses the prose path regardless of content.
    doc = "Section one is here.\n\nSection two follows with more detail.\n"
    chunks = docstore.chunk_document(doc, kind="brand")
    assert not any(t.startswith("Row ") for _, t in chunks)


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
