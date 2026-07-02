"""fr1.4 RLS tenant-isolation backstop (AC-2) — PG integration.

Proves that, as the NOSUPERUSER ``scalers_app`` role with ``app.current_tenant``
set, an unfiltered read returns ONLY the session tenant's rows and a write for
another tenant is refused — for each of memories / actions / contact_memories
(the bead's memories/actions/customers surfaces). Mirrors the kb_chunks RLS
test. Skips if the ``scalers_app`` role is not available on the cluster.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from memory.store import MemoryStore

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

DSN = os.environ.get("ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers")
UTC = timezone.utc
NOW = datetime(2026, 7, 3, 19, 0, tzinfo=UTC)
_INITDB = Path(__file__).resolve().parents[2] / "infra" / "initdb"


@pytest.fixture(scope="module", autouse=True)
def _rls_schema():
    # Apply the tables + the RLS migration to the shared DB (as superuser).
    with psycopg.connect(DSN, autocommit=True) as conn:
        for name in ("08-actions.sql", "16-suppression-consent.sql", "18-tenant-isolation.sql"):
            conn.execute((_INITDB / name).read_text(encoding="utf-8"))
    MemoryStore(DSN).ensure_schema()  # memories table + its RLS


def _app_dsn(dsn: str) -> str:
    parts = urlsplit(dsn)
    netloc = f"scalers_app:scalers_app@{parts.hostname}:{parts.port or 5432}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _app_conn(tenant: str):
    try:
        conn = psycopg.connect(_app_dsn(DSN))
    except psycopg.OperationalError as exc:
        pytest.skip(f"scalers_app role not available ({exc}); RLS backstop not testable here")
    conn.autocommit = True
    conn.execute("SELECT set_config('app.current_tenant', %s, false)", (tenant,))
    return conn


def _seed_superuser(sql: str, params: tuple) -> None:
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql, params)


# ── memories ─────────────────────────────────────────────────────────────────


def test_rls_memories_read_and_write_isolation():
    a, b = f"rls-a-{uuid.uuid4().hex[:8]}", f"rls-b-{uuid.uuid4().hex[:8]}"
    for t in (a, b):
        _seed_superuser(
            "INSERT INTO memories (id, tenant_id, subject_type, text, content_hash)"
            " VALUES (%s,%s,'customer',%s,%s)",
            (f"mem_{uuid.uuid4().hex[:12]}", t, f"note for {t}", uuid.uuid4().hex),
        )
    conn = _app_conn(a)
    try:
        rows = conn.execute("SELECT tenant_id FROM memories").fetchall()  # no WHERE
        assert rows and all(r[0] == a for r in rows)  # read isolation
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO memories (id, tenant_id, subject_type, text, content_hash)"
                " VALUES (%s,%s,'customer','x',%s)",
                (f"mem_{uuid.uuid4().hex[:12]}", b, uuid.uuid4().hex),
            )  # write for another tenant refused by WITH CHECK
    finally:
        conn.close()


# ── actions ──────────────────────────────────────────────────────────────────


def test_rls_actions_read_and_write_isolation():
    a, b = f"rls-a-{uuid.uuid4().hex[:8]}", f"rls-b-{uuid.uuid4().hex[:8]}"
    for t in (a, b):
        _seed_superuser(
            "INSERT INTO actions (id, tenant_id, type, channel, draft, status,"
            " idempotency_key) VALUES (%s,%s,'outreach','sms','hi','pending',%s)",
            (f"act_{uuid.uuid4().hex[:12]}", t, uuid.uuid4().hex),
        )
    conn = _app_conn(a)
    try:
        rows = conn.execute("SELECT tenant_id FROM actions").fetchall()
        assert rows and all(r[0] == a for r in rows)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO actions (id, tenant_id, type, channel, draft, status,"
                " idempotency_key) VALUES (%s,%s,'outreach','sms','x','pending',%s)",
                (f"act_{uuid.uuid4().hex[:12]}", b, uuid.uuid4().hex),
            )
    finally:
        conn.close()


# ── contact_memories (the "customers" contact surface) ───────────────────────


def test_rls_contact_memories_read_and_write_isolation():
    a, b = f"rls-a-{uuid.uuid4().hex[:8]}", f"rls-b-{uuid.uuid4().hex[:8]}"
    for t in (a, b):
        _seed_superuser(
            "INSERT INTO contact_memories (tenant_id, identifier, content, valid_from)"
            " VALUES (%s,%s,%s,%s)",
            (t, "+17025550001", '{"kind":"contact_preference"}', NOW),
        )
    conn = _app_conn(a)
    try:
        rows = conn.execute("SELECT tenant_id FROM contact_memories").fetchall()
        assert rows and all(r[0] == a for r in rows)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO contact_memories (tenant_id, identifier, content, valid_from)"
                " VALUES (%s,%s,%s,%s)",
                (b, "+17025550002", '{"kind":"contact_preference"}', NOW),
            )
    finally:
        conn.close()


def test_rls_forced_even_for_table_owner_semantics():
    # A scalers_app session with NO app.current_tenant set sees nothing (the
    # setting resolves to NULL, and tenant_id = NULL is never true) — fail-closed.
    a = f"rls-a-{uuid.uuid4().hex[:8]}"
    _seed_superuser(
        "INSERT INTO memories (id, tenant_id, subject_type, text, content_hash)"
        " VALUES (%s,%s,'customer','x',%s)",
        (f"mem_{uuid.uuid4().hex[:12]}", a, uuid.uuid4().hex),
    )
    try:
        conn = psycopg.connect(_app_dsn(DSN))
    except psycopg.OperationalError as exc:
        pytest.skip(f"scalers_app role not available ({exc})")
    conn.autocommit = True
    try:
        rows = conn.execute("SELECT tenant_id FROM memories WHERE tenant_id=%s", (a,)).fetchall()
        assert rows == []  # no app.current_tenant -> zero rows, never a leak
    finally:
        conn.close()
