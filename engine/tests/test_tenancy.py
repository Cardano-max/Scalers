"""fr1.4 per-request tenancy (AC-3) — resolver semantics (DB-free) + the DB seam.

The resolver takes the tenant ONLY from the authenticated principal and refuses
a cross-tenant request; an env var can never override it. The DB tests confirm
``set_current_tenant`` / ``tenant_connection`` drive the RLS session variable.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

from ops.tenancy import (
    CrossTenantError,
    resolve_request_tenant,
    set_current_tenant,
    tenant_connection,
)


def _principal(tenant: str):
    return SimpleNamespace(subject="agent-1", tenant_id=tenant)


# ── resolver semantics (DB-free) ─────────────────────────────────────────────


def test_resolver_returns_principal_tenant():
    assert resolve_request_tenant(_principal("tenant-a")) == "tenant-a"


def test_env_cannot_override_principal_tenant(monkeypatch):
    # The audit's failure mode: tenancy from STUDIO_TENANT_ID. The resolver
    # ignores env entirely — the principal is the only source of truth.
    monkeypatch.setenv("STUDIO_TENANT_ID", "tenant-evil")
    assert resolve_request_tenant(_principal("tenant-a")) == "tenant-a"


def test_requested_other_tenant_refused():
    with pytest.raises(CrossTenantError):
        resolve_request_tenant(_principal("tenant-a"), requested_tenant="tenant-b")


def test_requested_matching_tenant_ok():
    assert resolve_request_tenant(_principal("tenant-a"), requested_tenant="tenant-a") == "tenant-a"


def test_principal_without_tenant_refused():
    with pytest.raises(CrossTenantError):
        resolve_request_tenant(SimpleNamespace(subject="x", tenant_id=None))


# ── the DB seam (RLS session variable) ───────────────────────────────────────

pg = pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)
DSN = os.environ.get("ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers")


@pg
def test_set_current_tenant_drives_the_guc():
    import psycopg

    with psycopg.connect(DSN, autocommit=True) as conn:
        set_current_tenant(conn, "tenant-xyz")
        got = conn.execute("SELECT current_setting('app.current_tenant', true)").fetchone()[0]
        assert got == "tenant-xyz"


@pg
def test_tenant_connection_as_app_is_rls_scoped():
    # tenant_connection(as_app=True) connects as scalers_app with the tenant set,
    # so an unfiltered read is RLS-scoped. Mirrors the RLS suite; skips if the
    # role is unavailable.
    import psycopg
    from pathlib import Path

    from memory.store import MemoryStore

    initdb = Path(__file__).resolve().parents[2] / "infra" / "initdb"
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute((initdb / "18-tenant-isolation.sql").read_text(encoding="utf-8"))
    MemoryStore(DSN).ensure_schema()

    a = f"ten-a-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO memories (id, tenant_id, subject_type, text, content_hash)"
            " VALUES (%s,%s,'customer','x',%s)",
            (f"mem_{uuid.uuid4().hex[:12]}", a, uuid.uuid4().hex),
        )
    try:
        with tenant_connection(DSN, tenant_id=a, as_app=True) as conn:
            rows = conn.execute("SELECT tenant_id FROM memories").fetchall()
    except Exception as exc:  # scalers_app unavailable
        if "scalers_app" in str(exc) or "authentication" in str(exc).lower():
            pytest.skip(f"scalers_app role not available ({exc})")
        raise
    assert rows and all(r["tenant_id"] == a for r in rows)
