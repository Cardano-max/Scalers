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
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

# Official API hosts per provider name (the only bases a provider may call). The
# keys MUST match each provider's ``name`` (research/providers/*.py): a name with
# no entry resolves to an EMPTY allowlist and is fail-closed (SSRFError) — correct
# for unknown providers, but it means every shipped provider name needs a row here
# before its live client wires, or it self-rejects (gy2).
OFFICIAL_API_HOSTS: dict[str, frozenset[str]] = {
    "firecrawl": frozenset({"api.firecrawl.dev"}),
    "meta_ad_library": frozenset(
        {"graph.facebook.com", "api.foreplay.co"}  # Meta Ad Library + Foreplay (primary)
    ),
    # a9m.2 added these providers (ExaProvider 'exa', ForeplayProvider 'foreplay')
    # but no allowlist rows — without them assert_official_endpoint(url, 'exa'|
    # 'foreplay') hits the empty default and SSRF-rejects the provider's OWN base
    # the moment its live client wires (gy2). 'foreplay' is also reachable under the
    # combined 'meta_ad_library' provider above; this row is its standalone provider.
    "exa": frozenset({"api.exa.ai"}),
    "foreplay": frozenset({"api.foreplay.co"}),
    # Connector scaffolds (xeu): Meta Graph (FB/IG publish + comment replies) and
    # Gmail + the OAuth token hosts. Connect only to these (sec conn-scaffold req D).
    "facebook": frozenset({"graph.facebook.com", "graph.instagram.com", "www.facebook.com"}),
    "gmail": frozenset({"gmail.googleapis.com", "oauth2.googleapis.com", "accounts.google.com"}),
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


def _ip_obj_is_unsafe(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if an IP object is in a non-public range."""
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _ip_is_unsafe(host: str) -> bool:
    """True if ``host`` is a *canonical* IP literal in a non-public range."""
    try:
        return _ip_obj_is_unsafe(ipaddress.ip_address(host))
    except ValueError:
        return False  # not a canonical IP literal; other checks handle it


def _looks_like_numeric_host(host: str) -> bool:
    """True if ``host`` is a NON-canonical numeric IPv4 encoding — decimal int
    (``2130706433``), hex (``0x7f000001``), octal (``0177.0.0.1``), or short-form
    (``127.1``). These bypass ``ipaddress.ip_address`` but resolvers still treat
    them as IPs, so we **fail closed** and reject them outright (sec F1).

    Canonical dotted-quad / IPv6 literals never reach here — they are classified
    as IPs first and range-checked; a legitimate caller uses a canonical form or a
    DNS hostname, never an obfuscated integer.
    """
    h = host.strip().strip(".")
    if not h:
        return False
    low = h.lower()
    if low.startswith("0x") or ".0x" in low:  # any hex octet
        return True
    labels = h.split(".")
    return bool(labels) and all(lbl.isdigit() for lbl in labels)


def assert_safe_url(url: str) -> str:
    """Validate a URL we intend to fetch (or ask a provider to fetch). Raises
    :class:`SSRFError` on anything unsafe; returns the URL on success.

    Blocks: non-https schemes, embedded credentials, private/loopback/link-local/
    reserved/metadata targets, and **obfuscated numeric hosts** (decimal/hex/octal/
    short-form IPv4 — sec F1). This is the static SSRF guard the live ``fetch(url)``
    MUST pass first; the live provider MUST ALSO call :func:`assert_resolved_ips_safe`
    on the DNS result (sec F2) — a hostname can still resolve to a private IP.
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
    # Canonical IP literal -> range-check it directly.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if _ip_obj_is_unsafe(ip):
            raise SSRFError(f"non-public IP target rejected: {host}")
        return url  # canonical public IP literal
    # Not a canonical IP literal: reject obfuscated numeric encodings (fail closed).
    if _looks_like_numeric_host(host):
        raise SSRFError(f"obfuscated/numeric host rejected (possible SSRF): {host!r}")
    return url  # ordinary DNS hostname — live provider MUST recheck resolved IPs


def assert_resolved_ips_safe(addresses) -> None:
    """MANDATORY live-provider recheck (sec F2). After ``getaddrinfo(host)``, pass
    EVERY resolved address here: each must be a public IP or this raises
    :class:`SSRFError`. A DNS name like ``127.0.0.1.nip.io`` passes the static
    guard but resolves to loopback — only this catches it.

    The caller MUST then **pin the connection to a vetted resolved IP** (connect by
    IP, send the hostname via SNI/Host) so a rebind between check and connect
    cannot swap in a private address.
    """
    checked = False
    for addr in addresses:
        checked = True
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError as exc:
            raise SSRFError(f"unparseable resolved address: {addr!r}") from exc
        if _ip_obj_is_unsafe(ip):
            raise SSRFError(f"host resolves to non-public IP: {addr}")
    if not checked:
        raise SSRFError("no resolved addresses to validate")


def resolve_and_pin(
    host: str,
    *,
    port: int = 443,
    resolver: Callable[..., list] | None = None,
) -> str:
    """Resolve ``host``, validate EVERY resolved IP, and return ONE vetted IP to
    pin the connection to (sec's go-live suggestion). Routing the live fetch
    through this makes the F2 recheck impossible to skip: there is no path to a
    connect IP that did not pass :func:`assert_resolved_ips_safe`.

    The live client MUST connect to the returned IP (sending ``host`` via SNI /
    the Host header) so a DNS rebind between resolve and connect cannot swap in a
    private address. ``resolver`` is injectable for tests (defaults to
    ``socket.getaddrinfo``); it must return ``getaddrinfo``-shaped tuples.
    """
    resolve = resolver or socket.getaddrinfo
    try:
        infos = resolve(host, port, 0, socket.SOCK_STREAM)
    except OSError as exc:
        raise SSRFError(f"DNS resolution failed for {host!r}: {exc}") from exc
    addrs = [info[4][0] for info in infos]
    assert_resolved_ips_safe(addrs)  # raises on any non-public / empty
    return addrs[0]


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
