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


def test_seed_is_idempotent(tenant) -> None:
    a = d.seed_tenant_documents(tenant)
    b = d.seed_tenant_documents(tenant)
    assert a == b  # deterministic id
    assert len(d.active_docs_index(tenant)) == 1  # no duplicate row
