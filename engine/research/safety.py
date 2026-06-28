"""Network-safety guards for the research providers (bead 1mk.4, sec hardening).

Conditions the LIVE providers (Firecrawl, Foreplay/Meta-Ad-Library) MUST satisfy
before going live (sec):
  - **TLS-in-code** — only ``https://`` endpoints; the TLS-disabled upstream
    ``fetch.py`` was stripped, this re-asserts the opposite in code.
  - **official-API-only** — the API base host must be on the per-provider
    allowlist (no arbitrary hosts; no scraping).
  - **SSRF guard on fetch(url)** — any URL we ask a provider to fetch is checked:
    https-only, no embedded credentials, and **never** a private / loopback /
    link-local / reserved / cloud-metadata target.
  - **rate limits** — a token-bucket every provider call must pass, so we respect
    source ToS / API caps.
  - **key-from-pack** — keys come from the tenant pack secret / env, never a
    vendored ``.env`` (enforced where the provider reads its key; see providers).

Pure stdlib (``ipaddress`` / ``urllib.parse``), deterministic, testable. The live
provider additionally re-checks the *resolved* IP after DNS (eng), since a
hostname can resolve to a private address — that runtime check composes with this
static one; it does not replace it.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

# Official API hosts per provider name (the only bases a provider may call).
OFFICIAL_API_HOSTS: dict[str, frozenset[str]] = {
    "firecrawl": frozenset({"api.firecrawl.dev"}),
    "meta_ad_library": frozenset(
        {"graph.facebook.com", "api.foreplay.co"}  # Meta Ad Library + Foreplay (primary)
    ),
}

# Hostnames that are never legitimate fetch targets (SSRF / metadata vectors).
_BLOCKED_HOSTNAMES = frozenset(
    {"localhost", "metadata.google.internal", "metadata"}
)
_BLOCKED_SUFFIXES = (".local", ".internal", ".localhost")


class SSRFError(ValueError):
    """A URL failed the SSRF / TLS / official-host safety guard."""


class RateLimitError(RuntimeError):
    """A provider call exceeded its configured rate limit."""


def _host_is_blocked_name(host: str) -> bool:
    h = host.lower().strip(".")
    if h in _BLOCKED_HOSTNAMES:
        return True
    return any(h.endswith(suf) for suf in _BLOCKED_SUFFIXES)


def _ip_is_unsafe(host: str) -> bool:
    """True if ``host`` is an IP literal in a non-public range."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # not an IP literal; hostname checks handle it
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_safe_url(url: str) -> str:
    """Validate a URL we intend to fetch (or ask a provider to fetch). Raises
    :class:`SSRFError` on anything unsafe; returns the URL on success.

    Blocks: non-https schemes, embedded credentials, private/loopback/link-local/
    reserved/metadata targets. This is the SSRF guard the live ``fetch(url)`` and
    any 'fetch this page' provider call MUST pass first.
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise SSRFError(f"non-https URL rejected (TLS required): {url!r}")
    if parts.username or parts.password:
        raise SSRFError("credentials embedded in URL rejected")
    host = parts.hostname or ""
    if not host:
        raise SSRFError(f"missing host: {url!r}")
    if _host_is_blocked_name(host):
        raise SSRFError(f"internal/metadata host rejected: {host}")
    if _ip_is_unsafe(host):
        raise SSRFError(f"non-public IP target rejected: {host}")
    return url


def assert_official_endpoint(url: str, provider_name: str) -> str:
    """The API base a provider calls MUST be its official, allowlisted host over
    https (official-API-only). Raises :class:`SSRFError` otherwise."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise SSRFError(f"provider endpoint must be https: {url!r}")
    allowed = OFFICIAL_API_HOSTS.get(provider_name, frozenset())
    host = (parts.hostname or "").lower()
    if host not in allowed:
        raise SSRFError(
            f"provider '{provider_name}' may only call {sorted(allowed)}, got {host!r}"
        )
    return url


class RateLimiter:
    """A simple token-bucket every provider call must pass (respect API caps/ToS).

    Deterministic + testable: ``try_acquire`` takes the current monotonic time so
    tests don't depend on the wall clock. ``rate`` = sustained tokens/sec, ``burst``
    = bucket size.
    """

    def __init__(self, rate: float, burst: int) -> None:
        if rate <= 0 or burst <= 0:
            raise ValueError("rate and burst must be positive")
        self._rate = float(rate)
        self._burst = float(burst)
        self._tokens = float(burst)
        self._last: float | None = None

    def try_acquire(self, now: float, cost: float = 1.0) -> bool:
        """Refill by elapsed time, then take ``cost`` tokens if available."""
        if self._last is None:
            self._last = now
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        if self._tokens >= cost:
            self._tokens -= cost
            return True
        return False

    def acquire(self, now: float, cost: float = 1.0) -> None:
        """Like :meth:`try_acquire` but raises :class:`RateLimitError` when over."""
        if not self.try_acquire(now, cost):
            raise RateLimitError("rate limit exceeded")
