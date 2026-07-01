"""MCP error taxonomy for the tool contract.

The Model Context Protocol splits tool errors into two mechanisms (spec
2025-11-25, *server/tools* §"Error Handling"):

  * **Protocol errors** — standard JSON-RPC 2.0 errors for problems with the
    *request itself*: an unknown tool, or a malformed ``tools/call``. The model
    is unlikely to be able to fix these, so they surface as a JSON-RPC ``error``
    object (``code`` + ``message``).
  * **Tool execution errors** — reported *inside* the tool result with
    ``isError: true`` (never as a protocol error), so the calling model can see
    the failure and self-correct. Input-validation failures, access-control
    denials, an honest "source not connected", timeouts, and business-logic
    errors all live here.

This module encodes exactly that split. :class:`ProtocolError` carries a
JSON-RPC ``code``; every :class:`ToolExecutionError` carries a machine-readable
:attr:`~ToolExecutionError.status` that is also written verbatim to the audit
log, so a reviewer can grep the audit trail by outcome. Messages are honest and
never fabricate data — a not-connected source surfaces its real
"upload a CSV for now" message unchanged.
"""

from __future__ import annotations

# ── Audit / result status codes (one per possible call outcome) ──────────────
# These are the exact strings written to the audit log's ``status`` column and
# echoed in an error result's ``structuredContent.error.status``.
STATUS_OK = "ok"
STATUS_PROTOCOL_ERROR = "protocol_error"
STATUS_INVALID_INPUT = "invalid_input"
STATUS_ACCESS_DENIED = "access_denied"
STATUS_NOT_CONNECTED = "not_connected"
STATUS_TIMEOUT = "timeout"
STATUS_ERROR = "error"

# JSON-RPC 2.0 reserved error codes (subset used by the tool layer).
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602


class McpError(RuntimeError):
    """Base for every error the tool contract raises."""


class ProtocolError(McpError):
    """A JSON-RPC-level error about the request itself (not tool execution).

    Surfaced as a JSON-RPC ``error`` object, NOT an ``isError`` tool result —
    per spec these are things the model cannot self-correct (unknown tool,
    malformed request)."""

    status = STATUS_PROTOCOL_ERROR

    def __init__(self, message: str, *, code: int = JSONRPC_INVALID_PARAMS) -> None:
        super().__init__(message)
        self.code = code

    def to_jsonrpc_error(self) -> dict:
        """The ``error`` member of a JSON-RPC response."""
        return {"code": self.code, "message": str(self)}


class UnknownToolError(ProtocolError):
    """The requested tool name is not registered on this server."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Unknown tool: {name!r}", code=JSONRPC_METHOD_NOT_FOUND)


class ToolExecutionError(McpError):
    """A failure that occurred while running a tool — reported to the caller as a
    tool result with ``isError: true`` so the model can see and react to it.

    ``status`` is the machine-readable outcome (also the audit ``status``)."""

    status = STATUS_ERROR


class InputValidationError(ToolExecutionError):
    """The arguments failed validation (wrong type, missing/unknown field, over a
    length/size bound, or a forbidden control character). Rejected — never passed
    through to the wrapped source."""

    status = STATUS_INVALID_INPUT


class AccessDeniedError(ToolExecutionError):
    """The principal may not make this call — either the tool is outside its
    granted scope, or it asked for a tenant other than the one it is bound to
    (cross-tenant access is refused before any read runs)."""

    status = STATUS_ACCESS_DENIED


class NotConnectedError(ToolExecutionError):
    """A tool routed to a source whose backing integration is not connected yet
    (Stribe / Mini-App). Wraps the adapter's :class:`NotConfiguredError` message
    verbatim — an honest "not connected", never fabricated data."""

    status = STATUS_NOT_CONNECTED


class ToolTimeoutError(ToolExecutionError):
    """The tool did not complete within the server's per-call deadline."""

    status = STATUS_TIMEOUT


__all__ = [
    "STATUS_OK",
    "STATUS_PROTOCOL_ERROR",
    "STATUS_INVALID_INPUT",
    "STATUS_ACCESS_DENIED",
    "STATUS_NOT_CONNECTED",
    "STATUS_TIMEOUT",
    "STATUS_ERROR",
    "JSONRPC_METHOD_NOT_FOUND",
    "JSONRPC_INVALID_PARAMS",
    "McpError",
    "ProtocolError",
    "UnknownToolError",
    "ToolExecutionError",
    "InputValidationError",
    "AccessDeniedError",
    "NotConnectedError",
    "ToolTimeoutError",
]
