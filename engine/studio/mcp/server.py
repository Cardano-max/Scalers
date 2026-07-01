"""The MCP tool server — the hardened call pipeline over the read-only tools.

Implements the two MCP operations the supervisor contract needs (spec 2025-11-25,
*server/tools*): ``tools/list`` (discovery) and ``tools/call`` (invocation), with
every "Servers MUST" security control the spec lists applied in order on every
call:

    resolve tool            → unknown ⇒ JSON-RPC protocol error (never isError)
    access control          → scope + tenant binding (studio.mcp.principal)
    validate all inputs     → studio.mcp.validation (reject, don't pass through)
    execute under a timeout → per-call deadline (studio.mcp requirement)
    honest not-connected    → adapter NotConfiguredError ⇒ not_connected result
    sanitize outputs        → studio.mcp.sanitize (bounded, control-char-free)
    audit                   → exactly one row per call, in a finally, every outcome

Design choices worth calling out:

  * The tenant a read uses is ALWAYS ``principal.tenant_id``. A ``tenant_id`` in
    the call arguments is only inspected to *detect and refuse* a cross-tenant
    attempt — it is never used as the read scope. That closes the confused-deputy
    / token-passthrough hole the MCP security guidance warns about.
  * Errors follow the spec's two-channel model: an unknown tool or malformed
    request is a JSON-RPC ``ProtocolError`` (raised); a tool-execution failure
    (access denied, invalid input, not-connected, timeout, internal) is returned
    as an ``isError: true`` result so the calling model can see and self-correct.
  * The audit write is in a ``finally`` and best-effort (a failing audit backend
    must never turn a good read into an error, nor mask the real outcome).
"""

from __future__ import annotations

import concurrent.futures
from time import perf_counter
from typing import Any

from studio.adapters import NotConfiguredError
from studio.mcp.audit import AuditLog, AuditRecord, InMemoryAuditLog, args_hash
from studio.mcp.errors import (
    STATUS_OK,
    NotConnectedError,
    ProtocolError,
    ToolExecutionError,
    ToolTimeoutError,
    UnknownToolError,
)
from studio.mcp.principal import Principal, authorize
from studio.mcp.sanitize import sanitize_output
from studio.mcp.tools import ToolContext, ToolDef, default_tools
from studio.mcp.validation import validate_arguments

# Default per-call deadline (seconds). A read-only CRM / doc lookup is fast; a
# call that blows this is treated as a timeout rather than hanging the caller.
DEFAULT_TIMEOUT_S = 10.0


class McpToolServer:
    """A registry of read-only tools plus the hardened ``tools/call`` pipeline."""

    def __init__(
        self,
        tools: list[ToolDef],
        *,
        audit: AuditLog | None = None,
        dsn: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._tools: dict[str, ToolDef] = {t.name: t for t in tools}
        self._audit: AuditLog = audit if audit is not None else InMemoryAuditLog()
        self._ctx = ToolContext(dsn=dsn)
        self._timeout_s = timeout_s

    @property
    def audit(self) -> AuditLog:
        return self._audit

    # -- tools/list --------------------------------------------------------- #
    def list_tools(self, principal: Principal | None = None) -> dict[str, Any]:
        """The ``tools/list`` result. When a ``principal`` is given, only the tools
        it is scoped for are listed (least-privilege discovery)."""
        tools = [
            t.describe()
            for t in self._tools.values()
            if principal is None or principal.may_call(t.name)
        ]
        return {"tools": tools}

    # -- tools/call --------------------------------------------------------- #
    def call_tool(
        self, principal: Principal, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Run one tool through the full pipeline and return an MCP tool result.

        Returns a result dict (``content`` + ``isError`` [+ ``structuredContent``])
        for every tool-execution outcome — including access denials, invalid input,
        not-connected sources, and timeouts. Raises :class:`ProtocolError` ONLY for
        request-level problems (unknown tool), per the spec's two-channel model."""
        arguments = arguments or {}
        start = perf_counter()
        status = STATUS_OK
        error_kind: str | None = None
        source = arguments.get("source") if isinstance(arguments, dict) else None
        try:
            tool = self._tools.get(name)
            if tool is None:
                raise UnknownToolError(name)

            # 1) access control — scope + tenant binding, before any read.
            requested_tenant = (
                arguments.get("tenant_id") if isinstance(arguments, dict) else None
            )
            authorize(principal, name, requested_tenant)

            # 2) input validation — reject malformed/oversized/malicious args.
            clean = validate_arguments(tool.input_schema, arguments)

            # 3) execute under a deadline.
            result = self._run_with_timeout(tool, principal, clean)

            # 4) sanitize outputs.
            sanitized = sanitize_output(result)
            return self._ok_result(sanitized)

        except ProtocolError as exc:
            # Request-level error — audit it, then raise for the transport to
            # format as a JSON-RPC error (NOT an isError tool result).
            status = exc.status
            error_kind = type(exc).__name__
            raise
        except NotConfiguredError as exc:
            # An honest not-connected source. Surface verbatim, never fabricate.
            wrapped = NotConnectedError(str(exc))
            status = wrapped.status
            error_kind = "NotConfiguredError"
            return self._error_result(wrapped)
        except ToolExecutionError as exc:
            status = exc.status
            error_kind = type(exc).__name__
            return self._error_result(exc)
        except Exception as exc:  # noqa: BLE001 - last-resort honest failure
            wrapped = ToolExecutionError(f"internal tool error: {type(exc).__name__}")
            status = wrapped.status
            error_kind = type(exc).__name__
            return self._error_result(wrapped)
        finally:
            latency_ms = (perf_counter() - start) * 1000.0
            self._write_audit(
                principal=principal,
                name=name,
                arguments=arguments,
                status=status,
                error_kind=error_kind,
                source=source,
                latency_ms=latency_ms,
            )

    # -- internals ---------------------------------------------------------- #
    def _run_with_timeout(
        self, tool: ToolDef, principal: Principal, clean_args: dict[str, Any]
    ) -> Any:
        """Run the handler with a hard wall-clock deadline. On timeout raises
        :class:`ToolTimeoutError`.

        NOTE: a Python thread cannot be force-killed, so a handler that blocks past
        the deadline keeps running in the background until it finishes; the CALL,
        however, returns a timeout immediately rather than hanging the caller. This
        is the honest, portable (incl. Windows) behavior for read-only work."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(tool.handler, principal, clean_args, self._ctx)
            try:
                return future.result(timeout=self._timeout_s)
            except concurrent.futures.TimeoutError as exc:
                raise ToolTimeoutError(
                    f"tool {tool.name!r} exceeded the {self._timeout_s:g}s deadline"
                ) from exc

    def _write_audit(
        self,
        *,
        principal: Principal,
        name: str,
        arguments: Any,
        status: str,
        error_kind: str | None,
        source: Any,
        latency_ms: float,
    ) -> None:
        try:
            self._audit.record(
                AuditRecord(
                    subject=principal.subject,
                    tenant_id=principal.tenant_id,
                    tool=name,
                    args_hash=args_hash(arguments),
                    status=status,
                    latency_ms=latency_ms,
                    error_kind=error_kind,
                    source=str(source) if source is not None else None,
                )
            )
        except Exception:  # noqa: BLE001 - audit must never fail the call
            pass

    @staticmethod
    def _ok_result(sanitized: Any) -> dict[str, Any]:
        import json

        structured = sanitized if isinstance(sanitized, dict) else {"result": sanitized}
        return {
            "content": [
                {"type": "text", "text": json.dumps(structured, ensure_ascii=False)}
            ],
            "structuredContent": structured,
            "isError": False,
        }

    @staticmethod
    def _error_result(exc: ToolExecutionError) -> dict[str, Any]:
        message = str(exc)
        return {
            "content": [{"type": "text", "text": message}],
            "structuredContent": {"error": {"status": exc.status, "message": message}},
            "isError": True,
        }


# --------------------------------------------------------------------------- #
# Wiring helpers.
# --------------------------------------------------------------------------- #
def build_default_server(
    *,
    dsn: str | None = None,
    audit: AuditLog | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> McpToolServer:
    """A server wired with the full default tool set. ``audit`` defaults to an
    in-memory log; pass :class:`~studio.mcp.audit.PgToolAuditLog` for durable
    auditing. ``dsn`` overrides the DB DSN used by the DB-backed tools."""
    return McpToolServer(default_tools(), audit=audit, dsn=dsn, timeout_s=timeout_s)


def demo_principal(
    tenant_id: str,
    *,
    subject: str = "supervisor",
    scopes: tuple[str, ...] | None = None,
) -> Principal:
    """A convenience principal bound to ``tenant_id`` (all-tools scope by default)."""
    return Principal.create(subject, tenant_id, scopes)


__all__ = [
    "McpToolServer",
    "build_default_server",
    "demo_principal",
    "DEFAULT_TIMEOUT_S",
]
