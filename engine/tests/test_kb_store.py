"""Eval-KB store integration tests (KNOW-01 / rvy.2) — real Postgres+pgvector.

Covers the bead's acceptance edge cases: two-tenant isolation (examples AND
metrics), empty-KB cleanliness, idempotent re-ingest, label-version history, plus
RLS defense-in-depth via the non-superuser scalers_app role.

Marked `integration` + skipif(ENGINE_DATABASE_URL) (dhv.5 / PR #2 convention).
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from kb import (
    Direction,
    Engine,
    EvalMetric,
    KbStore,
    RunKind,
    Scope,
    Split,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

_SCHEMA = Path(__file__).resolve().parents[2] / "infra" / "initdb" / "03-eval-kb.sql"


@pytest.fixture
def kb_store(dsn) -> KbStore:
    """Apply the eval-KB schema (idempotent) + reset the three tables."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(_SCHEMA.read_text(encoding="utf-8"))
        conn.execute("TRUNCATE gold_example, gold_label, eval_metric")
    return KbStore(dsn)


def _seed_example(store: KbStore, tenant: str, topic: str, **kw):
    return store.upsert_gold_example(
        tenant_id=tenant, engine=Engine.POSTING, cell="content_brief",
        input={"topic": topic}, expected={"on_voice": True},
        rubric_dimensions=["voice"], split=Split.HOLDOUT, **kw,
    )


# ── Tenant isolation ─────────────────────────────────────────────────────────


def test_two_tenants_isolated_gold_examples(kb_store):
    _seed_example(kb_store, "tenant-a", "a-topic")
    _seed_example(kb_store, "tenant-b", "b-topic")

    a = kb_store.get_gold_set(tenant_id="tenant-a", engine=Engine.POSTING)
    assert len(a) == 1
    assert all(e.tenant_id == "tenant-a" for e in a)
    assert not any(e.input["topic"] == "b-topic" for e in a)


def test_two_tenants_isolated_metrics(kb_store):
    kb_store.record_metric(EvalMetric(metric="f1", value=0.97, tenant_id="tenant-a", engine="POSTING"))
    kb_store.record_metric(EvalMetric(metric="f1", value=0.50, tenant_id="tenant-b", engine="POSTING"))

    a = kb_store.get_metrics(tenant_id="tenant-a", metric="f1")
    assert len(a) == 1 and a[0].tenant_id == "tenant-a" and a[0].value == 0.97


# ── Empty KB ─────────────────────────────────────────────────────────────────


def test_empty_kb_returns_empty(kb_store):
    assert kb_store.get_gold_set(tenant_id="nobody", engine=Engine.OUTREACH) == []
    assert kb_store.get_metrics(tenant_id="nobody") == []
    assert kb_store.get_labels(tenant_id="nobody", example_id="00000000-0000-0000-0000-000000000000") == []


# ── Idempotent re-ingest ─────────────────────────────────────────────────────


def test_reingest_same_example_is_idempotent(kb_store):
    first = _seed_example(kb_store, "tenant-a", "same")
    second = _seed_example(kb_store, "tenant-a", "same")  # identical content
    assert first == second  # same row id
    assert len(kb_store.get_gold_set(tenant_id="tenant-a", engine=Engine.POSTING)) == 1


def test_label_reingest_is_idempotent(kb_store):
    eid = _seed_example(kb_store, "tenant-a", "label-me")
    kb_store.add_gold_label(example_id=eid, tenant_id="tenant-a", rater_id="r1", dimension="voice", label={"on_voice": True})
    kb_store.add_gold_label(example_id=eid, tenant_id="tenant-a", rater_id="r1", dimension="voice", label={"on_voice": False})
    labels = kb_store.get_labels(tenant_id="tenant-a", example_id=eid)
    assert len(labels) == 1 and labels[0].label == {"on_voice": False}  # refreshed, not dup


# ── Label-version history ────────────────────────────────────────────────────


def test_label_version_bump_keeps_old_metrics(kb_store):
    common = dict(metric="brand_voice_onvoice", tenant_id="tenant-a", engine="POSTING",
                  cell="content_brief", threshold=0.90, direction=Direction.GTE,
                  run_kind=RunKind.PER_PROMOTION)
    kb_store.record_metric(EvalMetric(value=0.91, label_version=1, **common))
    kb_store.record_metric(EvalMetric(value=0.93, label_version=2, **common))

    v1 = kb_store.get_metrics(tenant_id="tenant-a", metric="brand_voice_onvoice", label_version=1)
    v2 = kb_store.get_metrics(tenant_id="tenant-a", metric="brand_voice_onvoice", label_version=2)
    assert len(v1) == 1 and v1[0].value == 0.91  # old metric intact
    assert len(v2) == 1 and v2[0].value == 0.93
    assert len(kb_store.get_metrics(tenant_id="tenant-a", metric="brand_voice_onvoice")) == 2


def test_metric_passed_is_computed(kb_store):
    mid = kb_store.record_metric(EvalMetric(metric="ece", value=0.03, tenant_id="t", threshold=0.05, direction=Direction.LTE))
    assert kb_store.get_metrics(tenant_id="t", metric="ece")[0].passed is True
    kb_store.record_metric(EvalMetric(metric="ece", value=0.09, tenant_id="t", threshold=0.05, direction=Direction.LTE))
    eces = [m.value for m in kb_store.get_metrics(tenant_id="t", metric="ece") if not m.passed]
    assert eces == [0.09]


# ── Read-contract guards ─────────────────────────────────────────────────────


def test_get_metrics_requires_tenant_or_global(kb_store):
    with pytest.raises(ValueError):
        kb_store.get_metrics()  # neither tenant_id nor scope=GLOBAL


def test_global_metric_visible_without_tenant(kb_store):
    kb_store.record_metric(EvalMetric(metric="precision", value=0.96, scope=Scope.GLOBAL, engine="POSTING"))
    g = kb_store.get_metrics(scope=Scope.GLOBAL, metric="precision")
    assert len(g) == 1 and g[0].scope is Scope.GLOBAL and g[0].tenant_id is None


# ── RLS defense-in-depth (non-superuser scalers_app role) ────────────────────


def _app_dsn(dsn: str) -> str:
    parts = urlsplit(dsn)
    netloc = f"scalers_app:scalers_app@{parts.hostname}:{parts.port or 5432}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def test_rls_blocks_cross_tenant_for_app_role(kb_store, dsn):
    """As the non-superuser scalers_app role, an unfiltered SELECT returns only
    the session tenant's rows — RLS enforces isolation even if a query forgets
    the tenant predicate. (The DAL's WHERE filter is the always-on guarantee;
    this proves the DB-level backstop.)"""
    _seed_example(kb_store, "tenant-a", "a-only")
    _seed_example(kb_store, "tenant-b", "b-only")

    try:
        conn = psycopg.connect(_app_dsn(dsn))
    except psycopg.OperationalError as exc:
        pytest.skip(f"scalers_app role not available ({exc}); RLS backstop not testable here")

    try:
        conn.execute("SELECT set_config('app.current_tenant', 'tenant-a', false)")
        # No WHERE clause — RLS alone must scope the result to tenant-a.
        rows = conn.execute("SELECT tenant_id FROM gold_example").fetchall()
        assert rows and all(r[0] == "tenant-a" for r in rows)

        conn.execute("SELECT set_config('app.current_tenant', 'tenant-b', false)")
        rows = conn.execute("SELECT tenant_id FROM gold_example").fetchall()
        assert rows and all(r[0] == "tenant-b" for r in rows)
    finally:
        conn.close()
