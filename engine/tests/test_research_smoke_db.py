"""Live-ish smoke: the deterministic per-lead research path end-to-end vs the DB.

Runs against the REAL local Postgres (the ~1095 seeded ``skindesign`` consumers)
with NO Firecrawl and NO LLM: research disabled -> honest-empty, social disabled ->
honest-None, deterministic psych floor, deterministic draft. READ-ONLY on the
skindesign tenant; the one write test uses a throwaway tenant deleted on exit.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

import psycopg
import pytest

from research.protected_traits import (
    allowed_categories,
    build_first_party_corpus,
    trait_violations,
)
from studio.customer_research import (
    build_outreach_draft,
    contactable_leads,
    gather_social_context,
    lookup_lead,
    research_studio,
)
from studio.psych_profile import INSUFFICIENT, analyze_customer

DSN = "postgresql://scalers:scalers@localhost:5432/scalers"
TENANT = "skindesign"  # real client tenant — READ ONLY in this module


def _require_db() -> None:
    try:
        with psycopg.connect(DSN, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"local Postgres not reachable ({exc})", allow_module_level=True)


_require_db()


@pytest.fixture(autouse=True)
def _deterministic_paths(monkeypatch):
    # No LLM copy, no LLM analyst, no web research, no social fetch — the honest
    # deterministic floor is what this smoke exercises.
    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "0")
    monkeypatch.setenv("SCALERS_PSYCH_LLM", "0")
    monkeypatch.delenv("STUDIO_DEEP_RESEARCH", raising=False)
    monkeypatch.delenv("STUDIO_SOCIAL_RESEARCH", raising=False)


def _first_lead() -> dict:
    leads = contactable_leads(TENANT, limit=1, dsn=DSN)
    if not leads:
        pytest.skip("skindesign tenant has no contactable customers seeded")
    return leads[0]


def test_deterministic_lead_to_draft_end_to_end():
    facts = _first_lead()
    # Grounded facts shape (incl. the consent-relevant handles + memories keys).
    for key in ("customer_id", "name", "ig_handle", "linkedin_handle", "memories"):
        assert key in facts
    assert isinstance(facts["memories"], list)

    # Research disabled -> honest-empty; social disabled -> honest-None. No egress.
    assert research_studio(facts, enabled=False) == []
    assert gather_social_context(facts, enabled=False) is None

    # Deterministic psych floor: produced, grounded-or-insufficient, trait-clean.
    profile = analyze_customer(facts, use_llm=False)
    assert profile.customer_id == facts["customer_id"]
    assert profile.source == "deterministic"
    # The exact invariant _apply_trait_filter guarantees: no surviving field
    # carries a protected-trait assertion beyond the lead's own first-party data.
    allowed = allowed_categories(facts)
    fp_corpus = build_first_party_corpus(facts)
    for name, field in profile.scalar_fields():
        if field.signal == INSUFFICIENT:
            continue
        assert field.evidence, f"grounded field {name} carries no evidence"
        assert trait_violations(
            f"{field.value} {field.evidence}",
            allowed=allowed, first_party_corpus=fp_corpus,
        ) == [], f"field {name} asserts an unfiltered protected trait"

    # Deterministic draft: honest, grounded, nothing sent.
    out = build_outreach_draft(facts, goal="say hello", tenant_id=TENANT)
    assert out["customer_id"] == facts["customer_id"]
    assert out["draft"]
    assert out["copy_model"] == "deterministic_template"
    assert any(g.startswith("copy=deterministic") for g in out["grounding"])
    # No research citations can exist on the deterministic keyless path.
    assert not any(g.startswith("research:") for g in out["grounding"])


def test_lookup_lead_surfaces_real_memories_for_the_analyst():
    """Round-trip: a memory row written for a lead comes back via lookup_lead's
    memory_store plumbing (the exact facts['memories'] the analyst consumes)."""
    from kb.embedding import DeterministicEmbedder
    from memory import MemoryStore

    tenant = "test_tenant_" + uuid.uuid4().hex[:10]

    @contextmanager
    def _throwaway():
        try:
            yield
        finally:
            with psycopg.connect(DSN, autocommit=True) as conn:
                for table in ("memories", "customers"):
                    try:
                        conn.execute(
                            f"DELETE FROM {table} WHERE tenant_id = %s", (tenant,)
                        )
                    except psycopg.errors.UndefinedTable:
                        pass

    with _throwaway():
        cust_id = "test_mem_" + uuid.uuid4().hex[:10]
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO customers (id, tenant_id, name, email, email_opt_in, "
                "sms_opt_in) VALUES (%s, %s, %s, %s, true, false)",
                (cust_id, tenant, "Mem Smoke", f"{cust_id}@example.com"),
            )
        store = MemoryStore(dsn=DSN, embedder=DeterministicEmbedder())
        store.ensure_schema()
        memory_text = f"Replied warmly to the spring campaign ({cust_id})."
        store.write(
            tenant_id=tenant, subject_type="customer", subject_id=cust_id,
            text=memory_text, metadata={"kind": "campaign"},
        )

        facts = lookup_lead(tenant, customer_id=cust_id, dsn=DSN, memory_store=store)
        assert facts is not None
        assert [m["text"] for m in facts["memories"]] == [memory_text]

        # And the analyst genuinely consumes it: the memory text is corpus, so the
        # deterministic profile plus the corpus gate see a real 'memory' surface.
        from studio.psych_profile import SRC_MEMORY, _present_sources
        from studio.reason_history import extract_signals

        assert SRC_MEMORY in _present_sources(facts, extract_signals(None), None)
