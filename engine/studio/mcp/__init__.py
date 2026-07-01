"""``studio.mcp`` — a hardened, read-only MCP tool contract over the studio sources.

This package exposes the existing data sources (the CRM lead adapters, the
conversation/message adapters, the artist adapters, the substantiated-offers
store, and the persistent per-tenant assets/documents library) as a Model
Context Protocol *tool contract* that a supervisor agent can call, with the
security hardening the MCP spec requires.

Grounded in the Model Context Protocol specification, revision **2025-11-25**
(https://modelcontextprotocol.io/specification/2025-11-25/server/tools) and its
Security Best Practices companion
(https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices):

  * a Tool is ``{name, title, description, inputSchema (JSON Schema),
    annotations}``; discovery is ``tools/list``, invocation is ``tools/call``;
  * errors use two channels — JSON-RPC *protocol* errors for request-level
    problems (unknown tool) vs ``isError: true`` *tool-execution* results the
    model can self-correct from;
  * a tool server **MUST** validate all inputs, enforce access controls,
    rate-limit, and sanitize outputs, and callers **SHOULD** apply timeouts and
    log tool usage for audit.

We implement that contract with our OWN logic (no third-party MCP SDK / skill and
no ``jsonschema`` dependency): :mod:`~studio.mcp.validation` is a self-contained
input validator, :mod:`~studio.mcp.principal` is the tenant-binding + scope access
gate, :mod:`~studio.mcp.sanitize` bounds and de-fangs outputs, and
:mod:`~studio.mcp.audit` writes one row per call. The tools in
:mod:`~studio.mcp.tools` CONSUME the adapters read-only and never mutate, send, or
fabricate: a not-connected source (Stribe / Mini-App) surfaces its real
:class:`~studio.adapters.NotConfiguredError` as an honest ``not_connected`` error.

Typical use::

    from studio.mcp import build_default_server, demo_principal

    server = build_default_server()                 # in-memory audit by default
    principal = demo_principal("ladies8391")         # bound to one tenant
    server.list_tools(principal)                     # discovery
    result = server.call_tool(principal, "offers.list_offers", {})
"""

from __future__ import annotations

from studio.mcp.audit import (
    AuditLog,
    AuditRecord,
    InMemoryAuditLog,
    PgToolAuditLog,
    args_hash,
)
from studio.mcp.errors import (
    AccessDeniedError,
    InputValidationError,
    McpError,
    NotConnectedError,
    ProtocolError,
    ToolExecutionError,
    ToolTimeoutError,
    UnknownToolError,
)
from studio.mcp.principal import Principal, authorize
from studio.mcp.server import (
    McpToolServer,
    build_default_server,
    demo_principal,
)
from studio.mcp.tools import ToolContext, ToolDef, default_tools

__all__ = [
    # server + wiring
    "McpToolServer",
    "build_default_server",
    "demo_principal",
    "default_tools",
    "ToolDef",
    "ToolContext",
    # identity + access control
    "Principal",
    "authorize",
    # audit
    "AuditLog",
    "AuditRecord",
    "InMemoryAuditLog",
    "PgToolAuditLog",
    "args_hash",
    # errors
    "McpError",
    "ProtocolError",
    "UnknownToolError",
    "ToolExecutionError",
    "InputValidationError",
    "AccessDeniedError",
    "NotConnectedError",
    "ToolTimeoutError",
]
