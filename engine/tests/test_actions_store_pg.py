"""Postgres integration for the actions store (review-queue read model).

Proves record/list/get/update + the UNIQUE idempotency_key dedupe round-trip on a
REAL Postgres. Marked ``integration`` (CI's pgvector job; excluded from the DB-free
unit run) AND ``skipif`` no ``ENGINE_DATABASE_URL`` — same convention as the other
*_pg tests, so it neither hides in CI nor breaks a local DB-free run.
"""

from __future__ import annotations

import os
import uuid

import pytest

from actions import store

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]


@pytest.fixture(autouse=True)
def _schema():
    store.ensure_schema()


def _record(tenant: str, **kw) -> str:
    kw.setdefault("decision_id", None)
    kw.setdefault("type", "outreach")
    kw.setdefault("channel", "gmail")
    kw.setdefault("worker", "Outreach")
    kw.setdefault("target", "client@studio.example")
    kw.setdefault("draft", "Hi from Ladies First")
    kw.setdefault("subject", "Your custom piece")
    kw.setdefault("context", None)
    kw.setdefault("conf", 0.83)
    kw.setdefault("threshold", 0.85)
    kw.setdefault("esc_kind", "below_threshold")
    kw.setdefault("esc_label", "confidence 0.83 < 0.85")
    kw.setdefault("idempotency_key", f"k-{uuid.uuid4().hex}")
    return store.record_pending_action(tenant_id=tenant, **kw)


def test_record_get_round_trip():
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    aid = _record(tenant)
    assert aid.startswith("act_")

    got = store.get_action(aid)
    assert got is not None
    assert got.tenant_id == tenant
    assert got.channel == "gmail"
    assert got.status == "pending"
    assert got.subject == "Your custom piece"
    assert got.conf == pytest.approx(0.83)
    assert got.esc_kind == "below_threshold"


def test_idempotency_key_dedupes():
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    key = f"k-{uuid.uuid4().hex}"
    a1 = _record(tenant, idempotency_key=key)
    a2 = _record(tenant, idempotency_key=key, draft="a different draft")
    assert a1 == a2  # same logical action -> same id, no duplicate row
    assert len(store.list_actions(tenant)) == 1


def test_list_filters_by_status():
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    a_pending = _record(tenant)
    a_other = _record(tenant)
    store.update_status(a_other, "approved")

    pending = store.list_actions(tenant, status="pending")
    assert [r.id for r in pending] == [a_pending]
    assert {r.id for r in store.list_actions(tenant)} == {a_pending, a_other}


def test_is_seeded_defaults_false_and_persists_true():
    # Slice-5 honesty gate: a live row is is_seeded=false; a seed row persists true.
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    live = _record(tenant)
    seeded = _record(tenant, is_seeded=True)

    assert store.get_action(live).is_seeded is False
    assert store.get_action(seeded).is_seeded is True

    # The persisted column is queryable directly (the verify-gate query shape).
    import psycopg

    with psycopg.connect(store._dsn()) as conn:
        (n,) = conn.execute(
            "SELECT count(*) FROM actions WHERE tenant_id=%s "
            "AND COALESCE(is_seeded,false)=true AND status IN ('sent','approved','pending')",
            (tenant,),
        ).fetchone()
    assert n == 1  # only the explicitly-seeded row counts; the live row does not


def test_update_status_sets_fields_and_rejects_unknown():
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    aid = _record(tenant)
    out = store.update_status(
        aid, "sent", deep_link="https://mail.google.com/mail/u/0/#sent/m1",
        outcome_label="Sent", outcome_kind="success",
    )
    assert out.status == "sent"
    assert out.deep_link.endswith("#sent/m1")
    assert out.outcome_label == "Sent"

    with pytest.raises(ValueError):
        store.update_status(aid, "sent", bogus_column="x")
