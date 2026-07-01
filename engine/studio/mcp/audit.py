"""The tool-call AUDIT LOG — one row per ``tools/call``, whatever the outcome.

The MCP spec (2025-11-25, *server/tools* §"Security Considerations") calls for
tool usage to be "Log[ged] ... for audit purposes". This is that log. EVERY call
writes exactly one row — success, access denial, invalid input, not-connected,
timeout, or internal error — capturing:

    who (principal subject) · which tenant · which tool · a hash of the
    arguments · the result status · latency · the error kind (if any) · when.

Arguments are recorded as a **sha256 hash**, not verbatim. A leads CSV or a
``customer_id`` is PII; the token-passthrough guidance warns that an audit trail
that stores raw upstream data becomes its own liability. The hash still lets a
reviewer prove that two calls carried identical arguments, or correlate a call
with a client-side record, without the store holding the sensitive payload.

Two backends, same interface:

  * :class:`InMemoryAuditLog` — the default; deterministic and dependency-free,
    ideal for tests and the in-memory demo.
  * :class:`PgToolAuditLog` — durable Postgres, following the exact thin-psycopg
    convention of :mod:`studio.conversations` / :mod:`studio.team.store`: a NEW
    additive ``mcp_tool_audit`` table created with ``CREATE TABLE IF NOT EXISTS``
    (never altering a table another module owns), psycopg imported lazily.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

_DEFAULT_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"


def args_hash(arguments: Any) -> str:
    """A stable sha256 over the canonical JSON of ``arguments``.

    Deterministic (``sort_keys``) so identical arguments hash identically; falls
    back to a hash of the ``repr`` for the rare non-serializable input rather
    than raising (the audit write must never be the thing that fails a call)."""
    try:
        canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        canonical = repr(arguments)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class AuditRecord:
    """One audit row. ``id``/``ts`` default to a fresh uuid / now(UTC)."""

    subject: str
    tenant_id: str
    tool: str
    args_hash: str
    status: str
    latency_ms: float
    error_kind: str | None = None
    source: str | None = None
    id: str = field(default_factory=lambda: "mcpaud_" + uuid.uuid4().hex[:16])
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        return d


@runtime_checkable
class AuditLog(Protocol):
    """Anything that can durably (or in-memory) accept an :class:`AuditRecord`."""

    def record(self, rec: AuditRecord) -> None:
        ...


class InMemoryAuditLog:
    """A list-backed audit log — deterministic, dependency-free, test-friendly."""

    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def record(self, rec: AuditRecord) -> None:
        self.records.append(rec)

    def all(self) -> list[AuditRecord]:
        """Every recorded row, in call order."""
        return list(self.records)

    def for_tenant(self, tenant_id: str) -> list[AuditRecord]:
        return [r for r in self.records if r.tenant_id == tenant_id]


# Additive DDL — a NEW table this module fully owns. ``CREATE TABLE IF NOT
# EXISTS`` so it is a no-op on re-run and never touches another module's table.
_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS mcp_tool_audit (
    id          TEXT PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    subject     TEXT        NOT NULL,
    tenant_id   TEXT        NOT NULL,
    tool        TEXT        NOT NULL,
    args_hash   TEXT        NOT NULL,
    status      TEXT        NOT NULL,
    latency_ms  DOUBLE PRECISION NOT NULL DEFAULT 0,
    error_kind  TEXT,
    source      TEXT
);
CREATE INDEX IF NOT EXISTS mcp_tool_audit_tenant_idx ON mcp_tool_audit (tenant_id);
CREATE INDEX IF NOT EXISTS mcp_tool_audit_tool_idx   ON mcp_tool_audit (tool);
CREATE INDEX IF NOT EXISTS mcp_tool_audit_ts_idx     ON mcp_tool_audit (ts);
"""


class PgToolAuditLog:
    """Durable Postgres audit log (``mcp_tool_audit``). psycopg imported lazily."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or os.environ.get("ENGINE_DATABASE_URL") or _DEFAULT_DSN

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self._dsn, autocommit=True, row_factory=dict_row)

    def ensure_schema(self) -> None:
        """Apply the additive DDL (idempotent; safe on every boot)."""
        with self._connect() as conn:
            conn.execute(_AUDIT_DDL)

    def record(self, rec: AuditRecord) -> None:
        self.ensure_schema()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO mcp_tool_audit "
                "(id, ts, subject, tenant_id, tool, args_hash, status, latency_ms, "
                " error_kind, source) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                (
                    rec.id, rec.ts, rec.subject, rec.tenant_id, rec.tool,
                    rec.args_hash, rec.status, rec.latency_ms, rec.error_kind,
                    rec.source,
                ),
            )

    def list_rows(
        self, *, tenant_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Audit rows newest-first, optionally filtered by tenant.

        This is a server-side / operator read — the audit log is deliberately NOT
        exposed as an MCP tool. If it ever were, it MUST be scoped to
        ``principal.tenant_id`` (the audit trail is itself tenant-sensitive), the
        same isolation rule the data tools enforce; the ``tenant_id`` filter here
        is what such a scoped tool would pass."""
        self.ensure_schema()
        clause = "WHERE tenant_id = %s" if tenant_id else ""
        params: tuple = (tenant_id, limit) if tenant_id else (limit,)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, ts, subject, tenant_id, tool, args_hash, status, "
                "latency_ms, error_kind, source "
                f"FROM mcp_tool_audit {clause} ORDER BY ts DESC LIMIT %s",
                params,
            ).fetchall()
        return [dict(r) for r in rows]


__all__ = [
    "AuditRecord",
    "AuditLog",
    "InMemoryAuditLog",
    "PgToolAuditLog",
    "args_hash",
]
