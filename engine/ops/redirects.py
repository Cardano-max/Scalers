"""Tenant-level redirect pins (CustomerAcq-fr1.4, AC-4).

The SDT (Skin Design) tenant onboards in TEST mode: BOTH the SMS and Gmail
send redirects must stay PINNED on — no real recipient reachable — until the
operator flips a SPECIFIC campaign live (the t90.4 go-live path; cross-ref, not
implemented here). Env vars alone are not enough (a missing ``SMS_REDIRECT_TO``
would otherwise fail open to live); the pin is a tenant-level invariant read
from the ``tenants`` row, above any env or per-send flag.

This composes with the un-bypassable ``tenants.check_send_allowed`` gate: that
one refuses sends to non-allowlisted recipients; this one refuses taking a
pinned tenant LIVE at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from tenants import store as tenant_store

__all__ = [
    "RedirectPinnedError",
    "RedirectPins",
    "assert_send_not_pinned_live",
    "is_redirect_pinned",
    "provision_sdt_tenant",
    "tenant_redirect_pins",
]


class RedirectPinnedError(RuntimeError):
    """A live send was attempted for a tenant whose channel redirect is PINNED.
    Only a per-campaign operator go-live flip (t90.4) may unpin it. Refused."""


@dataclass(frozen=True)
class RedirectPins:
    """Whether each channel's redirect is pinned on for a tenant."""

    sms: bool
    gmail: bool


def tenant_redirect_pins(tenant_id: str, *, dsn: str | None = None) -> RedirectPins:
    """The tenant's redirect pins. A tenant with no row (or an unreachable
    registry) is treated as UNPINNED — pins are an explicit opt-in per tenant,
    exactly like ``test_mode`` (legacy tenants unchanged)."""
    row = tenant_store.get_tenant(tenant_id, dsn=dsn) or {}
    return RedirectPins(
        sms=bool(row.get("sms_redirect_pinned")),
        gmail=bool(row.get("gmail_redirect_pinned")),
    )


def is_redirect_pinned(tenant_id: str, channel: str, *, dsn: str | None = None) -> bool:
    """Whether ``channel`` ('sms' | 'gmail'/'email') is pinned for the tenant."""
    pins = tenant_redirect_pins(tenant_id, dsn=dsn)
    ch = (channel or "").strip().lower()
    if ch == "sms":
        return pins.sms
    if ch in ("gmail", "email"):
        return pins.gmail
    return False


def assert_send_not_pinned_live(
    tenant_id: str, channel: str, requested_live: bool, *, dsn: str | None = None
) -> None:
    """Raise :class:`RedirectPinnedError` when a LIVE send is requested for a
    tenant whose ``channel`` redirect is pinned. A redirected (test) send is
    always allowed; only going live is refused. No-op when ``requested_live`` is
    False (the sandbox default hot path — no DB lookup)."""
    if not requested_live:
        return
    if is_redirect_pinned(tenant_id, channel, dsn=dsn):
        raise RedirectPinnedError(
            f"tenant {tenant_id!r} has its {channel} redirect PINNED (test mode) — a "
            "live send is refused until an operator flips the campaign live (t90.4)"
        )


def provision_sdt_tenant(
    *, tenant_id: str = "skindesign", name: str = "Skin Design", dsn: str | None = None
) -> dict:
    """Provision the SDT tenant as tenant #2: TEST mode with BOTH redirects
    pinned. Idempotent (upsert). This is how Skin Design onboards safely — no
    real send is reachable until a per-campaign operator flip."""
    return tenant_store.upsert_tenant(
        tenant_id, name, test_mode=True, sms_redirect_pinned=True,
        gmail_redirect_pinned=True, dsn=dsn,
    )
