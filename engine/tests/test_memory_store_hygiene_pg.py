"""``MemoryStore`` test-artifact hygiene (wwy.9) — PG integration.

Mirrors the fr1.3 ``contact_memories`` de-pollution onto the Studio Host's
``memories`` table: an ``is_test`` flag, a pattern backfill (flag — never
delete), and recall/list defaults of ``is_test=false`` so a ``test_mem_*`` /
``test-stage-*`` artifact can never ground a real draft or Host prompt.

Runs in a PRIVATE per-process schema (``include_public=True`` so pgvector's
``vector`` type resolves) — the suite itself never touches the live tables.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from kb.embedding import DeterministicEmbedder
from memory import MemoryStore
from tests.conftest import private_schema

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

TENANT = "t"


@pytest.fixture
def store():
    with private_schema(include_public=True) as s:
        st = MemoryStore(dsn=s.dsn, embedder=DeterministicEmbedder())
        st.ensure_schema()
        yield st, s.dsn


def _write(st: MemoryStore, subject_id: str, text: str, **kw) -> str:
    return st.write(
        tenant_id=TENANT,
        subject_type="customer",
        subject_id=subject_id,
        text=text,
        **kw,
    )


def test_recall_defaults_to_exclude_test_memories(store):
    st, _ = store
    _write(st, "cust_real", "Prefers walk-ins on weekdays.")
    _write(st, "cust_real", "test_mem_ artifact injected by a suite run.", is_test=True)

    hits = st.recall(tenant_id=TENANT, query="preferences", subject_id="cust_real", k=10)
    assert hits and all("test_mem_" not in h.text for h in hits)
    assert all(h.is_test is False for h in hits)
    # Audit view still sees everything.
    allhits = st.recall(
        tenant_id=TENANT,
        query="preferences",
        subject_id="cust_real",
        k=10,
        include_test=True,
    )
    assert len(allhits) == 2


def test_list_for_subject_excludes_test_by_default(store):
    st, _ = store
    _write(st, "cust_x", "real note")
    _write(st, "cust_x", "synthetic note", is_test=True)
    real = st.list_for_subject(tenant_id=TENANT, subject_type="customer", subject_id="cust_x")
    assert [m.text for m in real] == ["real note"]
    both = st.list_for_subject(
        tenant_id=TENANT, subject_type="customer", subject_id="cust_x", include_test=True
    )
    assert len(both) == 2


def test_backfill_flags_all_three_patterns_never_deletes(store):
    st, dsn = store
    # Two real rows + three artifacts (by subject prefix, by staging session, by text residue).
    _write(st, "cust_a", "Asked about bridal flash pricing.")
    _write(st, "cust_b", "Wants a sleeve consult in August.")
    _write(st, "test_mem_deadbeef", "Prefers DM over email.")
    _write(
        st,
        "cust_a",
        "Staged instagram outreach for goal 'win back'.",
        metadata={"session_id": "test-stage-0123456789"},
    )
    _write(st, "cust_b", "note referencing test_mem_cafebabe residue")

    flagged = st.backfill_test_flags(tenant_id=TENANT)
    assert flagged == 3
    # Flag, never delete: all 5 rows still present.
    with psycopg.connect(dsn, autocommit=True) as c:
        total = c.execute("SELECT count(*) FROM memories").fetchone()[0]
        n_test = c.execute("SELECT count(*) FROM memories WHERE is_test=true").fetchone()[0]
    assert total == 5
    assert n_test == 3
    # Idempotent: a second pass flags nothing new.
    assert st.backfill_test_flags(tenant_id=TENANT) == 0
    # Post-backfill recall (the Host-prompt path) is clean.
    hits = st.recall(tenant_id=TENANT, query="anything", k=10)
    assert len(hits) == 2
    assert all("test_mem_" not in h.text for h in hits)


def test_ensure_schema_adds_is_test_to_preexisting_table(store):
    """The live-cluster path: a memories table created BEFORE wwy.9 (no is_test)
    must gain the column idempotently from ensure_schema's ALTER."""
    _, dsn = store
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("DROP TABLE memories")
        c.execute(
            """
            CREATE TABLE memories (
                id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                subject_type TEXT NOT NULL, subject_id TEXT, text TEXT NOT NULL,
                embedding vector(384), metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                content_hash TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    st = MemoryStore(dsn=dsn, embedder=DeterministicEmbedder())
    st.ensure_schema()
    st.ensure_schema()  # re-run safe
    _write(st, "cust_old", "written after migration", is_test=True)
    assert (
        st.list_for_subject(tenant_id=TENANT, subject_type="customer", subject_id="cust_old") == []
    )
    assert (
        len(
            st.list_for_subject(
                tenant_id=TENANT, subject_type="customer", subject_id="cust_old", include_test=True
            )
        )
        == 1
    )
