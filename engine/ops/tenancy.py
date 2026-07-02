"""Per-request tenancy (CustomerAcq-fr1.4, AC-3).

Tenant identity must come from the AUTHENTICATED PRINCIPAL, never a process env
var — the audit found tenancy resolved from ``STUDIO_TENANT_ID``, which any
process (or a confused request) can set. :func:`resolve_request_tenant` reads
the tenant ONLY from the principal and refuses a call that asks for a different
tenant (the confused-deputy guard, mirroring :mod:`studio.mcp.principal`). This
COMPOSES with C7's bearer-token auth (which mints the principal from the token)
— it does not duplicate it; when C7 lands, the principal comes from the token
and this resolver still holds.

:func:`set_current_tenant` / :func:`tenant_connection` are the DB seam: they set
the ``app.current_tenant`` session variable the RLS policies read, so a
NOSUPERUSER connection is scoped to exactly the resolved tenant. Mirrors the
existing ``KbStore._conn`` pattern.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg.rows import dict_row

__all__ = [
    "CrossTenantError",
    "app_role_dsn",
    "resolve_request_tenant",
    "set_current_tenant",
    "tenant_connection",
]

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"


class CrossTenantError(RuntimeError):
    """A request tried to act on a tenant other than the one its authenticated
    principal is bound to. Refused (confused-deputy guard)."""


def resolve_request_tenant(principal: Any, *, requested_tenant: str | None = None) -> str:
    """The tenant a request may act on: ``principal.tenant_id``, ALWAYS — never an
    env var, never a caller-asserted value the server did not establish. If the
    call carries a ``requested_tenant`` that differs from the principal's, it is
    refused. Env vars (e.g. ``STUDIO_TENANT_ID``) are deliberately ignored here."""
    tenant = getattr(principal, "tenant_id", None)
    if not tenant:
        raise CrossTenantError("principal has no tenant_id — cannot resolve request tenant")
    if requested_tenant is not None and requested_tenant != tenant:
        raise CrossTenantError(
            f"principal is bound to tenant {tenant!r} but the request asked for "
            f"{requested_tenant!r} — refusing (cross-tenant / confused-deputy)"
        )
    return tenant


def set_current_tenant(conn: psycopg.Connection, tenant_id: str, *, local: bool = False) -> None:
    """Set the ``app.current_tenant`` session variable the RLS policies read.
    ``local=True`` scopes it to the current transaction (``SET LOCAL`` semantics)."""
    conn.execute("SELECT set_config('app.current_tenant', %s, %s)", (tenant_id, local))


def app_role_dsn(dsn: str, *, user: str = "scalers_app", password: str = "scalers_app") -> str:
    """Rewrite ``dsn`` to connect as the NOSUPERUSER ``scalers_app`` role, so RLS
    is enforced (a superuser would bypass it)."""
    parts = urlsplit(dsn)
    netloc = f"{user}:{password}@{parts.hostname}:{parts.port or 5432}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


@contextmanager
def tenant_connection(
    dsn: str | None = None, *, tenant_id: str, as_app: bool = False
) -> Iterator[psycopg.Connection]:
    """A connection with ``app.current_tenant`` set to ``tenant_id``. With
    ``as_app`` it connects as ``scalers_app`` (RLS enforced); otherwise it uses
    the given DSN's role. The tenant should come from
    :func:`resolve_request_tenant`, not an env var."""
    resolved = dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN
    if as_app:
        resolved = app_role_dsn(resolved)
    conn = psycopg.connect(resolved, autocommit=True, row_factory=dict_row)
    try:
        set_current_tenant(conn, tenant_id)
        yield conn
    finally:
        conn.close()
