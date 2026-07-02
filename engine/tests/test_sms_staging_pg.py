"""SMS-3 per-RECIPIENT exactly-once staging (CustomerAcq-t90.3, P1) — PG integration.

Staging-time layer (a): the outbox gains (tenant_id, target, draft_md5) with a
partial UNIQUE index WHERE status='PENDING' plus a per-(tenant,target)
max-pending cap. A conflict returns the EXISTING id (never a second row) — the
bead's DB-proof repro (3 byte-identical pending drafts to one lead) yields 1 row.

Crash-window notes (AC 7): the dedupe is a DB unique index, not check-then-act
(W5); the cap count runs under a pg advisory xact lock so concurrent stagings
serialize (W6); a re-stage after a crash-and-retry hits the same key and returns
DUPLICATE (W1); key-scheme drift (the phase3 run-scoped-key bug) is caught by the
index because it does not depend on how the idempotency key was derived.
"""

from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest

from sideeffects.keys import Channel, idempotency_key
from sideeffects.staging import StageStatus, stage_sms_draft
from suppression.ledger import ensure_schema

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

DSN = os.environ.get("ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers")
_BOUNDARY_SQL = Path(__file__).resolve().parents[2] / "infra" / "initdb" / "02-side-effect-boundary.sql"

BODY = "SDT: July flash sale this week - book your session. Reply STOP to opt out."


@pytest.fixture(scope="module", autouse=True)
def _schema():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(_BOUNDARY_SQL.read_text(encoding="utf-8"))
    ensure_schema(DSN)


def _tenant() -> str:
    return f"t903s-{uuid.uuid4().hex[:10]}"


def _phone() -> str:
    return f"+1702555{uuid.uuid4().int % 10_000:04d}"


def _pending_count(tenant: str, target: str) -> int:
    with psycopg.connect(DSN, autocommit=True) as conn:
        return conn.execute(
            "SELECT count(*) FROM outbox WHERE tenant_id=%s AND target=%s AND status='PENDING'",
            (tenant, target),
        ).fetchone()[0]


def test_channel_enum_has_sms():
    assert Channel.SMS.value == "sms"


def test_three_byte_identical_drafts_yield_one_row():
    # The bead's DB proof: 3 byte-identical pending drafts to one lead. Now: 1 row.
    tenant, phone = _tenant(), _phone()
    results = [
        stage_sms_draft(tenant_id=tenant, target=phone, draft=BODY, dsn=DSN)
        for _ in range(3)
    ]
    assert results[0].status is StageStatus.STAGED
    assert results[1].status is StageStatus.DUPLICATE
    assert results[2].status is StageStatus.DUPLICATE
    # Conflict returns the EXISTING id, exactly like the idempotency-conflict path.
    assert results[1].outbox_id == results[0].outbox_id
    assert results[2].outbox_id == results[0].outbox_id
    assert _pending_count(tenant, phone) == 1


def test_stage_uses_content_scoped_idempotency_key():
    tenant, phone = _tenant(), _phone()
    result = stage_sms_draft(tenant_id=tenant, target=phone, draft=BODY, dsn=DSN)
    assert result.key == idempotency_key(tenant, Channel.SMS, phone, BODY)


def test_distinct_drafts_stage_until_cap_then_refused():
    tenant, phone = _tenant(), _phone()
    for i in range(3):
        r = stage_sms_draft(
            tenant_id=tenant, target=phone, draft=f"{BODY} v{i}", max_pending=3, dsn=DSN,
        )
        assert r.status is StageStatus.STAGED
    r = stage_sms_draft(
        tenant_id=tenant, target=phone, draft=f"{BODY} v99", max_pending=3, dsn=DSN,
    )
    assert r.status is StageStatus.CAP_EXCEEDED
    assert _pending_count(tenant, phone) == 3


def test_cap_is_per_tenant_target_pair():
    tenant, phone = _tenant(), _phone()
    stage_sms_draft(tenant_id=tenant, target=phone, draft=BODY, max_pending=1, dsn=DSN)
    # Same tenant, different target — unaffected by the first target's cap.
    other = _phone()
    r = stage_sms_draft(tenant_id=tenant, target=other, draft=BODY, max_pending=1, dsn=DSN)
    assert r.status is StageStatus.STAGED


def test_settled_identical_content_not_restaged():
    # Exactly-once outlives the pending state: the SAME promo content to the same
    # recipient stays sent-once (global idempotency key), returning the settled id.
    tenant, phone = _tenant(), _phone()
    first = stage_sms_draft(tenant_id=tenant, target=phone, draft=BODY, dsn=DSN)
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("UPDATE outbox SET status='SENT' WHERE id=%s", (first.outbox_id,))
    again = stage_sms_draft(tenant_id=tenant, target=phone, draft=BODY, dsn=DSN)
    assert again.status is StageStatus.DUPLICATE
    assert again.outbox_id == first.outbox_id
    assert _pending_count(tenant, phone) == 0


def test_key_scheme_drift_still_deduped_by_partial_index():
    # Defense-in-depth for the EXACT phase3 bug: a row staged with a RUN-SCOPED
    # key (different key, same tenant/target/content, PENDING) must still
    # conflict — the partial unique index does not care how the key was derived.
    tenant, phone = _tenant(), _phone()
    with psycopg.connect(DSN, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO outbox (idempotency_key, channel, payload, status,"
            " tenant_id, target, draft_md5)"
            " VALUES (%s, 'sms', %s, 'PENDING', %s, %s, md5(%s)) RETURNING id",
            (f"run-42:{phone}", json.dumps({"body": BODY}), tenant, phone, BODY),
        ).fetchone()
    r = stage_sms_draft(tenant_id=tenant, target=phone, draft=BODY, dsn=DSN)
    assert r.status is StageStatus.DUPLICATE
    assert r.outbox_id == row[0]
    assert _pending_count(tenant, phone) == 1


def test_boundary_style_sms_insert_without_identity_columns_rejected():
    # Adversarial-review F4: the generic boundary enqueue inserts NULL
    # tenant_id/target/draft_md5, which would bypass the partial unique index
    # (NULLs are distinct). The DDL CHECK makes the DATABASE refuse an sms
    # outbox row without its recipient identity — the bypass cannot recur.
    with psycopg.connect(DSN, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO outbox (idempotency_key, channel, payload, status)"
                " VALUES (%s, 'sms', %s, 'PENDING')",
                (f"nulls:{uuid.uuid4().hex}", json.dumps({"body": "x"})),
            )


def test_concurrent_staging_respects_cap():
    # W6: 10 concurrent DISTINCT drafts against a cap of 3 — the advisory xact
    # lock serializes the count+insert, so exactly 3 land.
    tenant, phone = _tenant(), _phone()

    def _stage(i: int):
        return stage_sms_draft(
            tenant_id=tenant, target=phone, draft=f"{BODY} concurrent {i}",
            max_pending=3, dsn=DSN,
        )

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(_stage, range(10)))
    staged = [r for r in results if r.status is StageStatus.STAGED]
    refused = [r for r in results if r.status is StageStatus.CAP_EXCEEDED]
    assert len(staged) == 3
    assert len(refused) == 7
    assert _pending_count(tenant, phone) == 3


def test_concurrent_identical_drafts_yield_one_row():
    # W5: dedupe is the DB index, not check-then-act — concurrent identical
    # stagings produce one row and everyone gets the same id.
    tenant, phone = _tenant(), _phone()

    def _stage(_: int):
        return stage_sms_draft(tenant_id=tenant, target=phone, draft=BODY, dsn=DSN)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_stage, range(8)))
    assert _pending_count(tenant, phone) == 1
    ids = {r.outbox_id for r in results}
    assert len(ids) == 1
    assert sum(1 for r in results if r.status is StageStatus.STAGED) == 1
