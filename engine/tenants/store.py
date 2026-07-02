"""Tenant registry: per-tenant TEST-MODE flag + test-send allowlist (ju1.1).

The ``tenants`` table is the SERVER-SIDE source of truth for whether a tenant may
send at all. ``test_mode=true`` (the default for new rows) means every send for the
tenant is refused at the connector boundary (see ``actions.publish``) unless the
recipient is on the tenant's explicit operator-approved ``test_send_allowlist``
(empty by default). A tenant with NO row behaves as before (legacy passthrough) —
the gate is opt-in per tenant, so ladies8391 is untouched.

Schema also ships as ``infra/initdb/12-tenants.sql``; ``ensure_schema`` here is the
idempotent runtime twin (CREATE TABLE IF NOT EXISTS), matching actions/store.py.
DSN from ``ENGINE_DATABASE_URL``.
"""

from __future__ import annotations

import json
import os
from typing import Any

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

_DDL = """
CREATE TABLE IF NOT EXISTS tenants (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    test_mode           BOOLEAN NOT NULL DEFAULT TRUE,
    test_send_allowlist JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def _dsn(dsn: str | None) -> str:
    return dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN


def _connect(dsn: str | None = None):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(dsn), row_factory=dict_row, autocommit=True)


def ensure_schema(dsn: str | None = None) -> None:
    """Idempotently create the ``tenants`` table."""
    with _connect(dsn) as conn:
        conn.execute(_DDL)


def upsert_tenant(
    tenant_id: str,
    name: str,
    *,
    test_mode: bool | None = None,
    allowlist: list[str] | None = None,
    dsn: str | None = None,
) -> dict[str, Any]:
    """Create or update a tenant row. ``test_mode``/``allowlist`` update only when
    passed (None leaves the persisted value alone — a re-import can never silently
    un-hold a tenant)."""
    ensure_schema(dsn)
    with _connect(dsn) as conn:
        conn.execute(
            """
            INSERT INTO tenants (id, name, test_mode, test_send_allowlist)
            VALUES (%s, %s, COALESCE(%s, TRUE), COALESCE(%s::jsonb, '[]'::jsonb))
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                test_mode = COALESCE(%s, tenants.test_mode),
                test_send_allowlist = COALESCE(%s::jsonb, tenants.test_send_allowlist),
                updated_at = now()
            """,
            (
                tenant_id,
                name,
                test_mode,
                json.dumps(allowlist) if allowlist is not None else None,
                test_mode,
                json.dumps(allowlist) if allowlist is not None else None,
            ),
        )
    return get_tenant(tenant_id, dsn=dsn)  # type: ignore[return-value]


def get_tenant(tenant_id: str, dsn: str | None = None) -> dict[str, Any] | None:
    """The tenant row as a dict, or None (no row = legacy passthrough tenant).
    Best-effort on a DB without the table yet: returns None rather than raising —
    an unreachable registry must never make the send path crash open."""
    try:
        with _connect(dsn) as conn:
            row = conn.execute(
                "SELECT id, name, test_mode, test_send_allowlist FROM tenants WHERE id=%s",
                (tenant_id,),
            ).fetchone()
    except Exception:
        return None
    return dict(row) if row else None


def check_send_allowed(
    tenant_id: str | None, recipient: str | None, dsn: str | None = None
) -> tuple[bool, str]:
    """The server-side TEST-MODE send gate decision.

    ``(True, reason)`` when the send may proceed; ``(False, reason)`` when it must
    be refused. Rules: no tenant row -> allowed (legacy tenants unchanged);
    ``test_mode`` false -> allowed; ``test_mode`` true -> allowed ONLY when the
    recipient is on the tenant's explicit allowlist (case-insensitive exact match;
    empty by default, so every real-customer send is refused)."""
    if not tenant_id:
        return True, "no tenant id on action (legacy passthrough)"
    row = get_tenant(tenant_id, dsn=dsn)
    if row is None:
        return True, f"tenant {tenant_id!r} has no registry row (legacy passthrough)"
    if not row.get("test_mode"):
        return True, f"tenant {tenant_id!r} test_mode off"
    allow = [str(a).strip().lower() for a in (row.get("test_send_allowlist") or [])]
    rec = (recipient or "").strip().lower()
    if rec and rec in allow:
        return True, f"recipient on tenant {tenant_id!r} test-send allowlist"
    return False, (
        f"TEST MODE - real customer sends disabled for tenant {tenant_id!r}; "
        f"recipient {recipient!r} is not on the operator-approved test allowlist"
    )
