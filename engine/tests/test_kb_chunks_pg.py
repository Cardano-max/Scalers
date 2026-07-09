"""kb_chunks voice-grounding retrieval (KNOW-02 / a9m.3) — real Postgres+pgvector.

Covers the bead's acceptance edges: two-tenant isolation (tenant A's read returns
ZERO tenant-B exemplars), empty-KB degrade (read returns [] without error, and the
assembly degrades to the pack ref), holdout exclusion (§1 disjoint invariant),
idempotent re-ingest, and RLS defense-in-depth via the non-superuser scalers_app role.

Marked `integration` + skipif(ENGINE_DATABASE_URL) (PR #2 convention, mirrors
test_kb_store.py).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from config.schema import TenantPack, VoiceRef
from kb import KbStore, build_voice_grounding
from tests.conftest import private_schema

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

@pytest.fixture
def kb_schema():
    """A PRIVATE per-process schema with the kb_chunks DDL applied (only 06,
    which creates the scalers_app role itself). ``include_public=True`` keeps
    pgvector's ``vector`` type resolvable; the table itself is created in the
    private schema, so this fixture never reads, writes, or wipes the LIVE
    ``public.kb_chunks`` KB (CustomerAcq-wwy.9)."""
    with private_schema("06-kb-content.sql", include_public=True) as s:
        yield s


@pytest.fixture
def kb_store(kb_schema) -> KbStore:
    return KbStore(kb_schema.dsn)


# A temp skills bundle so build_voice_grounding can resolve dimensions without
# depending on the (writer-owned) checked-in bundle being merged yet.
_FILL = {
    "dimensions": {
        "tone": ["warm, direct"],
        "structure": ["one idea per line"],
        "vocabulary": {"prefer": ["made for you"], "ban": ["slay"], "approved_claims": []},
    }
}


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    for tenant in ("tenant-a", "tenant-b"):
        d = tmp_path / "brand-voice" / "tenants" / tenant
        d.mkdir(parents=True)
        (d / "voice-dimensions.json").write_text(json.dumps(_FILL), encoding="utf-8")
    return tmp_path


def _pack(tenant: str) -> TenantPack:
    return TenantPack(tenant_id=tenant, display_name=tenant, voice=VoiceRef(skill=f"brand-voice/{tenant}"))


# ── tenant isolation (the headline AC) ────────────────────────────────────────


def test_two_tenants_isolated_voice_exemplars(kb_store):
    kb_store.upsert_kb_chunk(tenant_id="tenant-a", content="A's neo-trad floral cover-up story", kind="post")
    kb_store.upsert_kb_chunk(tenant_id="tenant-b", content="B's Brooklyn fine-line minimalism", kind="post")

    a = kb_store.voice_exemplars(tenant_id="tenant-a", query="cover-up", k=10)
    assert a, "tenant-a should retrieve its own chunk"
    assert all("Brooklyn" not in e.content for e in a)  # ZERO tenant-b content
    assert all("A's" in e.content for e in a)


def test_empty_kb_returns_empty_without_error(kb_store):
    assert kb_store.voice_exemplars(tenant_id="nobody", query="anything", k=5) == []


def test_holdout_chunks_excluded_from_grounding(kb_store):
    # The disjoint invariant: a chunk the rvy.4 holdout scores against must never be
    # returned as grounding.
    kb_store.upsert_kb_chunk(tenant_id="t", content="grounding-safe sample", kind="voice")
    kb_store.upsert_kb_chunk(tenant_id="t", content="HOLDOUT graded sample", kind="voice", is_holdout=True)

    got = kb_store.voice_exemplars(tenant_id="t", query="sample", k=10)
    contents = [e.content for e in got]
    assert "grounding-safe sample" in contents
    assert "HOLDOUT graded sample" not in contents


def test_reingest_same_chunk_is_idempotent(kb_store):
    first = kb_store.upsert_kb_chunk(tenant_id="t", content="same chunk", kind="post")
    second = kb_store.upsert_kb_chunk(tenant_id="t", content="same chunk", kind="post")
    assert first == second  # same row id, no dup
    assert len(kb_store.voice_exemplars(tenant_id="t", query="same", k=10)) == 1


def test_similarity_is_one_minus_cosine(kb_store):
    kb_store.upsert_kb_chunk(tenant_id="t", content="exact query text", kind="post")
    got = kb_store.voice_exemplars(tenant_id="t", query="exact query text", k=1)
    assert got and 0.0 <= got[0].similarity <= 1.0001
    # identical text -> near-1 similarity (1 - ~0 cosine distance).
    assert got[0].similarity > 0.99


# ── assembly over the real store ──────────────────────────────────────────────


def test_build_grounding_full_over_real_kb(kb_store, skills_root):
    for i in range(5):
        kb_store.upsert_kb_chunk(tenant_id="tenant-a", content=f"past floral post {i}", kind="post")
    g = build_voice_grounding(_pack("tenant-a"), kb_store, query="floral", k=5, skills_root=skills_root)
    assert g.coverage.value == "full" and g.exemplar_count == 5 and not g.low_grounding
    assert g.dimensions.tone == ["warm, direct"]


def test_build_grounding_empty_kb_degrades_to_pack_ref(kb_store, skills_root):
    # New tenant, no past content: read degrades to dimensions-only + low_grounding,
    # never errors, never fabricates generic copy.
    g = build_voice_grounding(_pack("tenant-b"), kb_store, query="anything", k=5, skills_root=skills_root)
    assert g.coverage.value == "sparse" and g.low_grounding is True and g.exemplars == []
    assert g.dimensions.vocabulary.ban == ["slay"]  # still grounded on the pack ref


# ── RLS defense-in-depth (non-superuser scalers_app role) ────────────────────


def _app_dsn(dsn: str) -> str:
    parts = urlsplit(dsn)
    netloc = f"scalers_app:scalers_app@{parts.hostname}:{parts.port or 5432}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def test_rls_blocks_cross_tenant_for_app_role(kb_store, kb_schema):
    """As scalers_app (non-superuser), an unfiltered SELECT returns only the session
    tenant's rows — RLS enforces isolation even if a query forgets the predicate.
    Connects with the PRIVATE-schema DSN (the search_path options survive
    ``_app_dsn``) so the read hits the fixture's table, never the live one."""
    kb_store.upsert_kb_chunk(tenant_id="tenant-a", content="a-only", kind="post")
    kb_store.upsert_kb_chunk(tenant_id="tenant-b", content="b-only", kind="post")

    try:
        conn = psycopg.connect(_app_dsn(kb_schema.dsn))
    except psycopg.OperationalError as exc:
        pytest.skip(f"scalers_app role not available ({exc}); RLS backstop not testable here")

    try:
        conn.execute("SELECT set_config('app.current_tenant', 'tenant-a', false)")
        rows = conn.execute("SELECT tenant_id FROM kb_chunks").fetchall()  # no WHERE
        assert rows and all(r[0] == "tenant-a" for r in rows)

        conn.execute("SELECT set_config('app.current_tenant', 'tenant-b', false)")
        rows = conn.execute("SELECT tenant_id FROM kb_chunks").fetchall()
        assert rows and all(r[0] == "tenant-b" for r in rows)
    finally:
        conn.close()
