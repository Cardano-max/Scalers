"""conversation_leads warm-cohort resolution — Postgres integration (skips without a DB).

Proves the no-CSV provided-leads cohort resolves the tenant's WARM leads (customers WITH
conversation history — Sarah Kim + the seeded tattoo cohort), NOT an empty churn set, so a
fresh provided-leads run from the console actually produces per-lead drafts.
"""

from __future__ import annotations

import os

import pytest

_DSN = (
    os.environ.get("ENGINE_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or "postgresql://scalers:scalers@localhost:5432/scalers"
)


def _db_or_skip():
    try:
        import psycopg

        psycopg.connect(_DSN, connect_timeout=3).close()
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"no Postgres for integration test: {exc}")


def test_conversation_leads_resolves_the_seeded_warm_cohort_incl_sarah() -> None:
    _db_or_skip()
    from memory import MemoryStore
    from studio.customer_research import conversation_leads
    from studio.seed_tattoo_leads import seed_warm_leads

    seed_warm_leads("ladies8391", dsn=_DSN)  # idempotent
    store = MemoryStore(dsn=_DSN)
    store.ensure_schema()

    leads = conversation_leads("ladies8391", limit=20, dsn=_DSN, memory_store=store)
    ids = {f["customer_id"] for f in leads}
    # The warm cohort is NON-empty and includes Sarah Kim (the price-objection SMS lead).
    # Resolve her id by EMAIL (the seeder's stable natural key): upsert_lead mints a
    # random cust_ id per DB, so a hardcoded id only ever matched the original dev DB.
    assert len(leads) >= 1
    from studio.customer_research import lookup_lead

    sarah = lookup_lead("ladies8391", email="sarah.kim@example.com", dsn=_DSN)
    assert sarah is not None, "seed_warm_leads did not resolve Sarah Kim by email"
    assert sarah["customer_id"] in ids  # Sarah Kim
    # Every resolved lead carries grounded facts (real customer, not a fabricated stub).
    assert all(f.get("customer_id") and f.get("name") for f in leads)
