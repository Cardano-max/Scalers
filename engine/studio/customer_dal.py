"""Tenant-scoped customer existence check — the verify-before-write primitive shared
by the staging idempotency guard (nmh.11) and inbound capture (tlv.2).

CONTRACT (agreed eng2/eng4 — must stay byte-identical across both callers until tlv.2
imports this and drops its private copy):
  * ``customer_exists`` returns a plain ``bool``;
  * the check is a TENANT-SCOPED ``SELECT 1 FROM customers WHERE tenant_id=%s AND id=%s``;
  * there is NO ``try/except`` — a DB error PROPAGATES so a transient failure surfaces
    as 5xx/retry, and is NEVER silently read as "customer does not exist".

Deliberately its own tiny module: tlv.2 must not depend on ``actions`` (so this does
not import from ``actions.store``), and it stays off the hot ``customer_research.py``
path.
"""

from __future__ import annotations

import os

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"


def _connect(dsn: str | None = None):
    import psycopg
    from psycopg.rows import dict_row

    resolved = dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN
    return psycopg.connect(resolved, autocommit=True, row_factory=dict_row)


def customer_exists(tenant_id: str, customer_id: str, dsn: str | None = None) -> bool:
    """True iff a customer with ``customer_id`` exists for ``tenant_id``.

    Verify-before-write primitive: a caller stages/writes for a customer ONLY after this
    returns True. Raises on any DB error (never returns a silent False) so a transient
    failure retries instead of laundering into a phantom "not found"."""
    with _connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM customers WHERE tenant_id = %s AND id = %s LIMIT 1",
            (tenant_id, customer_id),
        ).fetchone()
    return row is not None
