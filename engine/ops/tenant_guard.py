"""Dev/prod tenant write-guard (CustomerAcq-fr1.3, AC-4).

The audit's motivating incident: a probe wrote a junk row into the CLIENT
tenant DURING the audit. This guard makes that impossible at the write
boundary — a process may only write the tenant it explicitly declares via the
``STUDIO_TENANT_ID`` env var, and any tenant listed in ``PROTECTED_TENANT_IDS``
(the real client tenants) is unwritable unless the declaration matches it
exactly. A dev process with no declaration can freely write dev/test tenants
(so SDT onboarding into a dedicated non-protected test-mode tenant is
unblocked) but can NEVER touch a protected client tenant.

The guard reads the environment on each call (never caches) so a test can set
and clear it per case, and so a long-lived worker re-pointed at a different
tenant is caught immediately.
"""

from __future__ import annotations

import os

__all__ = ["TenantWriteBlocked", "assert_tenant_writable", "protected_tenant_ids"]


class TenantWriteBlocked(RuntimeError):
    """A write was attempted against a tenant this process is not authorized to
    write (wrong ``STUDIO_TENANT_ID``, or a protected tenant without a matching
    declaration). The write must not proceed."""


def protected_tenant_ids() -> frozenset[str]:
    """The real client tenants that are write-protected, from
    ``PROTECTED_TENANT_IDS`` (comma-separated, whitespace-tolerant)."""
    raw = os.environ.get("PROTECTED_TENANT_IDS", "")
    return frozenset(t.strip() for t in raw.split(",") if t.strip())


def assert_tenant_writable(tenant_id: str) -> None:
    """Raise :class:`TenantWriteBlocked` unless this process may write
    ``tenant_id``. Rules:

    * If ``STUDIO_TENANT_ID`` is set, it is the ONLY tenant this process may
      write — any other tenant is blocked (catches a process pointed at the
      wrong tenant, the audit incident).
    * A tenant in ``PROTECTED_TENANT_IDS`` may be written ONLY when
      ``STUDIO_TENANT_ID`` equals it (an explicit operator declaration).
    * Otherwise (dev/test tenant, no declaration) the write is allowed.
    """
    declared = os.environ.get("STUDIO_TENANT_ID")
    if declared:
        if tenant_id != declared:
            raise TenantWriteBlocked(
                f"process declared STUDIO_TENANT_ID={declared!r} but attempted a "
                f"write to tenant {tenant_id!r} — refusing (dev/prod tenant guard)"
            )
        return
    if tenant_id in protected_tenant_ids():
        raise TenantWriteBlocked(
            f"tenant {tenant_id!r} is protected (PROTECTED_TENANT_IDS); writes "
            "require an explicit matching STUDIO_TENANT_ID declaration — refusing"
        )
