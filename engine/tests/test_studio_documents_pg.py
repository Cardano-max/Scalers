"""Postgres integration for the persistent tenant document store.

Proves the real round-trip on a REAL Postgres: add → list/index → ts_rank retrieve →
deactivate → gone from index AND unreachable by retrieval. Marked ``integration`` +
``skipif`` no ``ENGINE_DATABASE_URL`` — same convention as the other *_pg tests.
"""

from __future__ import annotations

import os
import uuid

import pytest

from studio import documents as d

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

_DOC = (
    "# Brand Voice\n\nLadies First is a woman-owned Austin appointment-only color "
    "tattoo studio. We write warm and direct, never girlboss hype.\n\n"
    "## Guardrails\n\nNever promise painless tattoos. No unapproved discount codes; "
    "neo-traditional color saturation is the craft.\n"
)


@pytest.fixture()
def tenant():
    tid = f"itest_docs_{uuid.uuid4().hex[:8]}"
    d.ensure_schema()
    yield tid
    # cleanup (cascade drops chunks)
    with d._connect() as c:
        c.execute("DELETE FROM tenant_documents WHERE tenant_id=%s", (tid,))


def test_add_list_retrieve_remove_round_trip(tenant) -> None:
    doc_id = d.add_document(tenant, "Brand Playbook", _DOC, kind="brand")

    idx = d.active_docs_index(tenant)
    assert [x["name"] for x in idx] == ["Brand Playbook"]
    assert idx[0]["chunks"] >= 2  # chunked by heading

    hits = d.retrieve(tenant, "neo-traditional color saturation", k=3)
    assert hits, "expected a lexical match for a phrase present in the doc"
    assert hits[0]["document_id"] == doc_id
    assert "color" in hits[0]["content"].lower()

    # soft-remove drops it from the index AND from retrieval (every surface)
    assert d.deactivate_document(tenant, doc_id) is True
    assert d.active_docs_index(tenant) == []
    assert d.retrieve(tenant, "neo-traditional color saturation", k=3) == []
    # second remove is a no-op (already inactive) — honest False
    assert d.deactivate_document(tenant, doc_id) is False


_CSV = (
    "name,email,city,notes\n"
    "Monolith Tattoo,appointments@monolith.com,Austin,asked about cover-ups\n"
    "Ink & Co,hello@inkco.com,Dallas,wants a consult\n"
)


def test_csv_chunks_per_row_and_retrieves_a_specific_row(tenant) -> None:
    # A CSV uploaded as kind='csv' is chunked one passage per data row, gets a
    # truthful summary, and an individual row is retrievable by a cell value.
    doc_id = d.add_document(tenant, "Leads", _CSV, kind="csv")

    idx = d.active_docs_index(tenant)
    assert idx[0]["chunks"] == 2  # two data rows, one passage each
    assert idx[0]["summary"] == "CSV: 2 rows; columns: name, email, city, notes"

    hits = d.retrieve(tenant, "Monolith cover-ups", k=3)
    assert hits, "expected to retrieve the specific lead row by its cell values"
    assert hits[0]["document_id"] == doc_id
    assert "Monolith Tattoo" in hits[0]["content"]
    assert hits[0]["heading"] == "Row 1"

    # soft-remove drops the CSV (and its rows) from every surface
    assert d.deactivate_document(tenant, doc_id) is True
    assert d.retrieve(tenant, "Monolith cover-ups", k=3) == []


def test_seed_is_idempotent() -> None:
    # The seed is the FIXTURE demo playbook — gated to fixture tenants only so it can
    # never seed a real client's RAG (CustomerAcq-wwy.7). A non-fixture tenant now gets
    # None (that path is asserted in test_fixture_bleed). Verify idempotency on a
    # fixture tenant ('demo', the studio default), scoped to the seed doc id so the
    # assertion is exact regardless of any other docs the tenant may hold.
    tid = "demo"
    d.ensure_schema()
    seed_id = d._seed_doc_id(tid)

    def _drop_seed():
        with d._connect() as c:
            c.execute("DELETE FROM tenant_documents WHERE tenant_id=%s AND id=%s",
                      (tid, seed_id))

    _drop_seed()
    try:
        a = d.seed_tenant_documents(tid)
        b = d.seed_tenant_documents(tid)
        assert a == b == seed_id                       # deterministic id, seed succeeded
        seeded = [x for x in d.active_docs_index(tid) if x["id"] == seed_id]
        assert len(seeded) == 1                         # no duplicate row
    finally:
        _drop_seed()


def test_seed_refuses_non_fixture_tenant(tenant) -> None:
    # The gate: a non-fixture tenant (the itest_docs_* random tenant) is NEVER seeded
    # with the fixture playbook — returns None and writes nothing.
    assert d.seed_tenant_documents(tenant) is None
    assert d.active_docs_index(tenant) == []
