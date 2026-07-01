"""The calling identity + the access-control gate.

Every tool call is made *by a principal* — a supervisor agent (or a user) that
has been issued access to exactly ONE tenant and a set of tool scopes. Two
independent checks run before any wrapped source is touched:

  * **Tenant binding (isolation).** A principal is bound to a single
    ``tenant_id``. The server ALWAYS reads with ``principal.tenant_id`` and never
    with a tenant value supplied in the call arguments. If a call *does* carry a
    ``tenant_id`` argument and it differs from the principal's, the call is
    refused outright. This mirrors the MCP security guidance against the
    "confused deputy" / token-passthrough anti-patterns (spec 2025-11-25,
    *basic/security_best_practices*): the server must not act on a caller-asserted
    identity it did not itself establish. So one tenant can never read another
    tenant's leads / conversations / offers / assets.

  * **Scope (least privilege).** A principal only carries the tool scopes it
    needs. ``"*"`` grants all tools; otherwise a tool name must be explicitly
    listed. This is the spec's scope-minimization recommendation — a compromised
    principal's blast radius is bounded to its granted tools.

Both refusals raise :class:`~studio.mcp.errors.AccessDeniedError`, which the
server turns into an ``isError`` tool result *and* an ``access_denied`` audit row
— a blocked cross-tenant attempt is always recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from studio.mcp.errors import AccessDeniedError

# The wildcard scope — a principal holding it may call any registered tool.
SCOPE_ALL = "*"


@dataclass(frozen=True)
class Principal:
    """Who is calling, which tenant they may read, and which tools they may use.

    Frozen + hashable so it is safe to pass around and use as a stable audit
    subject. ``scopes`` is the set of tool names granted (or ``{"*"}`` for all).
    """

    subject: str
    tenant_id: str
    scopes: frozenset[str] = frozenset({SCOPE_ALL})

    def __post_init__(self) -> None:
        if not self.subject or not str(self.subject).strip():
            raise ValueError("Principal.subject must be a non-empty identifier")
        if not self.tenant_id or not str(self.tenant_id).strip():
            raise ValueError("Principal.tenant_id must be a non-empty tenant id")

    @classmethod
    def create(
        cls, subject: str, tenant_id: str, scopes: Iterable[str] | None = None
    ) -> "Principal":
        """Construct a principal, defaulting to the all-tools scope when unset."""
        scope_set = frozenset(scopes) if scopes is not None else frozenset({SCOPE_ALL})
        return cls(subject=subject, tenant_id=tenant_id, scopes=scope_set)

    def may_call(self, tool_name: str) -> bool:
        """True iff this principal is scoped for ``tool_name``."""
        return SCOPE_ALL in self.scopes or tool_name in self.scopes


def authorize(
    principal: Principal, tool_name: str, requested_tenant: str | None
) -> None:
    """Enforce scope + tenant binding for one call. Raises
    :class:`AccessDeniedError` on refusal; returns ``None`` when allowed.

    ``requested_tenant`` is any ``tenant_id`` the caller put in the arguments. It
    is used ONLY to detect and reject a cross-tenant attempt — the actual read
    always uses ``principal.tenant_id`` (never this value)."""
    if not principal.may_call(tool_name):
        raise AccessDeniedError(
            f"principal {principal.subject!r} is not scoped for tool "
            f"{tool_name!r} (least-privilege)"
        )
    if requested_tenant is not None and str(requested_tenant) != principal.tenant_id:
        raise AccessDeniedError(
            f"cross-tenant access refused: principal {principal.subject!r} is "
            f"bound to tenant {principal.tenant_id!r} but the call requested "
            f"tenant {str(requested_tenant)!r}"
        )


__all__ = ["Principal", "authorize", "SCOPE_ALL"]
