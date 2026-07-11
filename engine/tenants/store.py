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

import psycopg

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"

# Tenants whose sandbox is SAFETY-CRITICAL: a MISSING registry row (fresh DB,
# missed migration, id typo) must be REFUSED, never treated as legacy
# passthrough. skindesign holds 1,093 real customers (CustomerAcq-wwy.4).
PROTECTED_TENANTS: frozenset[str] = frozenset({"skindesign"})

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


def set_tenant_send_mode(
    tenant_id: str,
    *,
    allow: str | None = None,
    test_mode: bool | None = None,
    dsn: str | None = None,
) -> dict[str, Any]:
    """The operator's two send-posture moves. Additive and explicit — never a bulk reset.

    ``allow`` APPENDS one recipient to the test-send allowlist (idempotent; the rest of the
    list is preserved). TEST MODE stays on, so the engine will really send to that one
    address and still refuse every other — which is how you prove a real delivery without
    putting the client's whole customer list at risk.

    ``test_mode=False`` takes the tenant out of test mode entirely. Callers are expected to
    have demanded an explicit confirmation first; this function does not send anything, and
    approve-first, the exactly-once claim and the per-customer consent checks all still
    stand in front of every actual delivery.

    Returns the tenant's REAL persisted posture afterwards — never an echo of the request."""
    ensure_schema(dsn)
    row = get_tenant(tenant_id, dsn=dsn) or {}
    current = list(row.get("test_send_allowlist") or [])
    if allow:
        target = allow.strip()
        if target and target.lower() not in {a.strip().lower() for a in current}:
            current.append(target)
    out = upsert_tenant(
        tenant_id,
        str(row.get("name") or tenant_id),
        test_mode=test_mode,
        allowlist=current if allow else None,
        dsn=dsn,
    )
    return {
        "testMode": bool(out.get("test_mode", True)),
        "allowlist": list(out.get("test_send_allowlist") or []),
    }


def _legacy_passthrough_allowlist() -> frozenset[str]:
    """Tenants explicitly allowed to legacy-passthrough with NO registry row,
    from ``TEST_MODE_LEGACY_PASSTHROUGH`` (comma-separated). Everything NOT on
    this list is refused when it has no row — passthrough is opt-in, never the
    silent default (CustomerAcq-wwy.4)."""
    raw = os.environ.get("TEST_MODE_LEGACY_PASSTHROUGH", "")
    return frozenset(t.strip() for t in raw.split(",") if t.strip())


def get_tenant(tenant_id: str, dsn: str | None = None) -> dict[str, Any] | None:
    """The tenant row as a dict, or ``None`` ONLY when the row is genuinely
    absent (the query ran and returned nothing).

    FAIL CLOSED (wwy.4): a read error (unreachable registry, missing table,
    permission) now PROPAGATES instead of being swallowed to ``None``. The old
    ``except Exception: return None`` silently turned a transient failure into a
    "no row -> legacy passthrough" — disabling the sandbox for real customers.
    The caller (:func:`check_send_allowed`) decides how to fail closed."""
    with _connect(dsn) as conn:
        row = conn.execute(
            "SELECT id, name, test_mode, test_send_allowlist FROM tenants WHERE id=%s",
            (tenant_id,),
        ).fetchone()
    return dict(row) if row else None


def check_send_allowed(
    tenant_id: str | None, recipient: str | None, dsn: str | None = None
) -> tuple[bool, str]:
    """The server-side TEST-MODE send gate decision — FAIL CLOSED (wwy.4).

    ``(True, reason)`` only when the send is positively cleared; ``(False,
    reason)`` otherwise. Rules:

    * registry read error -> REFUSE (never silently pass);
    * missing table -> ``ensure_schema`` + retry once, then apply the rules;
    * NO row: a :data:`PROTECTED_TENANTS` tenant is REFUSED; any other tenant is
      refused UNLESS explicitly listed in ``TEST_MODE_LEGACY_PASSTHROUGH``;
    * ``test_mode`` false -> allowed;
    * ``test_mode`` true -> allowed ONLY when the recipient is on the tenant's
      explicit allowlist (empty by default, so every real-customer send is refused).
    """
    if not tenant_id:
        return True, "no tenant id on action (legacy passthrough)"
    try:
        row = get_tenant(tenant_id, dsn=dsn)
    except psycopg.errors.UndefinedTable:
        # Fresh DB / missed migration: create the table once and retry. If the
        # retry ALSO fails, refuse — never fall through to passthrough.
        try:
            ensure_schema(dsn)
            row = get_tenant(tenant_id, dsn=dsn)
        except Exception:
            return False, (
                f"tenant registry unreachable for {tenant_id!r} — refusing (fail closed)"
            )
    except Exception:
        return False, (
            f"tenant registry unreachable for {tenant_id!r} — refusing (fail closed)"
        )

    if row is None:
        if tenant_id in PROTECTED_TENANTS:
            return False, (
                f"tenant {tenant_id!r} has NO registry row and is PROTECTED — refusing "
                "(fail closed; provision the tenant row before sending)"
            )
        if tenant_id in _legacy_passthrough_allowlist():
            return True, (
                f"tenant {tenant_id!r} legacy passthrough (explicit "
                "TEST_MODE_LEGACY_PASSTHROUGH allowlist)"
            )
        return False, (
            f"tenant {tenant_id!r} has no registry row — refusing (fail closed); add a "
            "tenants row, or list it in TEST_MODE_LEGACY_PASSTHROUGH for legacy passthrough"
        )
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
