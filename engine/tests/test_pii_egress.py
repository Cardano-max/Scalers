"""fr1.4 PII egress check (AC-5) — DB-free source assertion.

The invariant (docs/security/pii-egress.md): customer PII lives ONLY in our
Postgres — no hosted memory cloud. This test fails if a future change introduces
an external memory/PII egress client into the memory or ledger write/recall
paths, so the invariant is enforced in CI, not just documented.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ENGINE = Path(__file__).resolve().parents[1]

# Hosted memory / vector-cloud / LLM-memory clients that would take PII off our
# Postgres. Outbound send connectors (twilio/gmail/meta) are intentionally NOT
# here — they are the send path, governed by the redirect pins, not a PII store.
_BANNED_EGRESS = (
    "pinecone", "weaviate", "chromadb", "qdrant_client", "mem0", "zep_python",
    "langchain.memory", "openai", "cohere", "anthropic.", "httpx", "requests",
)

_PII_MODULES = (
    "memory/store.py",
    "suppression/ledger.py",
)


@pytest.mark.parametrize("rel", _PII_MODULES)
def test_pii_module_has_no_hosted_cloud_egress(rel):
    # Scan IMPORT lines only (not prose) so a word like "requests" in a comment
    # is not a false positive — an egress client has to actually be imported.
    import_lines = [
        ln.strip().lower()
        for ln in (_ENGINE / rel).read_text(encoding="utf-8").splitlines()
        if ln.lstrip().startswith(("import ", "from "))
    ]
    hits = [
        marker
        for marker in _BANNED_EGRESS
        for ln in import_lines
        if marker in ln
    ]
    assert not hits, f"{rel} imports hosted-cloud egress: {sorted(set(hits))}"


def test_pii_egress_doc_exists():
    doc = _ENGINE.parent / "docs" / "security" / "pii-egress.md"
    assert doc.exists()
    assert "no hosted memory cloud" in doc.read_text(encoding="utf-8").lower()


def test_default_embedder_is_local():
    # The memory embedder must be a LOCAL model, not a hosted embedding API —
    # embedding a memory can't ship its text to a third party.
    import inspect

    from kb import embedding

    src = inspect.getsource(embedding).lower()
    assert "openai" not in src and "cohere" not in src
