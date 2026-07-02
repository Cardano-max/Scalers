"""OPS-3 per-process private test schema + tenant guard at the DB (fr1.3, AC-4) — PG.

The proven fix for shared-Postgres cross-process pollution (the audit's junk-row
incident): each test process runs in its OWN schema via ``search_path`` baked
into the DSN, so two processes cannot see or truncate each other's rows. This
file proves the isolation AND that the tenant guard, wired into the real ledger
write path, refuses a write to a protected client tenant.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg
import pytest

from ops.tenant_guard import TenantWriteBlocked
from suppression.ledger import record_suppression
from tests.conftest import private_schema

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

UTC = timezone.utc
NOW = datetime(2026, 7, 2, 19, 0, tzinfo=UTC)


def test_two_private_schemas_do_not_see_each_others_rows():
    with private_schema("02-side-effect-boundary.sql", "14-suppression-consent.sql") as a, \
         private_schema("02-side-effect-boundary.sql", "14-suppression-consent.sql") as b:
        assert a.schema != b.schema
        record_suppression(
            tenant_id="t", identifier="+17025550001", channel="sms", reason="stop",
            raw_utterance="STOP", occurred_at=NOW, dsn=a.dsn,
        )
        with psycopg.connect(a.dsn, autocommit=True) as ca:
            na = ca.execute("SELECT count(*) FROM suppression_ledger").fetchone()[0]
        with psycopg.connect(b.dsn, autocommit=True) as cb:
            nb = cb.execute("SELECT count(*) FROM suppression_ledger").fetchone()[0]
        assert na == 1
        assert nb == 0  # schema b never saw schema a's write


def test_private_schema_dropped_on_teardown():
    with private_schema("02-side-effect-boundary.sql", "14-suppression-consent.sql") as s:
        schema = s.schema
    with psycopg.connect(
        os.environ["ENGINE_DATABASE_URL"], autocommit=True
    ) as c:
        exists = c.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name=%s", (schema,)
        ).fetchone()
    assert exists is None


def test_tenant_guard_refuses_protected_tenant_at_ledger_write(monkeypatch):
    monkeypatch.delenv("STUDIO_TENANT_ID", raising=False)
    monkeypatch.setenv("PROTECTED_TENANT_IDS", "skindesign-prod")
    with private_schema("02-side-effect-boundary.sql", "14-suppression-consent.sql") as s:
        with pytest.raises(TenantWriteBlocked):
            record_suppression(
                tenant_id="skindesign-prod", identifier="+17025550009", channel="sms",
                reason="stop", raw_utterance="STOP", occurred_at=NOW, dsn=s.dsn,
            )
        # Nothing was written — the guard fired before the INSERT.
        with psycopg.connect(s.dsn, autocommit=True) as c:
            n = c.execute("SELECT count(*) FROM suppression_ledger").fetchone()[0]
        assert n == 0


def test_tenant_guard_allows_declared_tenant_at_ledger_write(monkeypatch):
    monkeypatch.setenv("STUDIO_TENANT_ID", "skindesign-prod")
    monkeypatch.setenv("PROTECTED_TENANT_IDS", "skindesign-prod")
    with private_schema("02-side-effect-boundary.sql", "14-suppression-consent.sql") as s:
        record_suppression(
            tenant_id="skindesign-prod", identifier="+17025550010", channel="sms",
            reason="stop", raw_utterance="STOP", occurred_at=NOW, dsn=s.dsn,
        )
        with psycopg.connect(s.dsn, autocommit=True) as c:
            n = c.execute("SELECT count(*) FROM suppression_ledger").fetchone()[0]
        assert n == 1
