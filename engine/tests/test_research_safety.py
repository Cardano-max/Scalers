"""Network-safety guard tests (bead 1mk.4 sec hardening) — DB-free, hermetic.

Asserts the conditions the live providers MUST meet before going live: SSRF guard
on fetch(url), TLS-only, official-API-only endpoints, and a working rate limiter.
"""

from __future__ import annotations

import pytest

from research import (
    FirecrawlProvider,
    MetaAdLibraryProvider,
    RateLimiter,
    RateLimitError,
    SSRFError,
    assert_official_endpoint,
    assert_resolved_ips_safe,
    assert_safe_url,
)


# ── SSRF guard on fetch(url) ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        "http://example.com/x",                 # non-https
        "https://localhost/admin",              # loopback name
        "https://127.0.0.1/",                   # loopback IP
        "https://10.0.0.5/internal",            # private
        "https://192.168.1.1/",                 # private
        "https://169.254.169.254/latest/meta-data/",  # cloud metadata
        "https://[::1]/",                       # ipv6 loopback
        "https://metadata.google.internal/",    # metadata host
        "https://svc.internal/",                # .internal suffix
        "https://user:pass@example.com/",       # embedded credentials
    ],
)
def test_ssrf_guard_blocks_unsafe(bad):
    with pytest.raises(SSRFError):
        assert_safe_url(bad)


@pytest.mark.parametrize(
    "ok",
    [
        "https://www.reddit.com/r/tattoos/",
        "https://www.pinterest.com/search/pins/?q=fineline",
        "https://api.firecrawl.dev/v1/scrape",
    ],
)
def test_ssrf_guard_allows_public_https(ok):
    assert assert_safe_url(ok) == ok


# ── F1: obfuscated IPv4 encodings (sec re-vet finding) ───────────────────────


@pytest.mark.parametrize(
    "obfuscated",
    [
        "https://2130706433/",        # decimal 127.0.0.1
        "https://0x7f000001/",        # hex
        "https://0177.0.0.1/",        # octal
        "https://127.1/",             # short form
        "https://0x7f.0.0.1/",        # mixed hex octet
        "https://3232235521/",        # decimal 192.168.0.1
    ],
)
def test_f1_obfuscated_numeric_hosts_blocked(obfuscated):
    with pytest.raises(SSRFError):
        assert_safe_url(obfuscated)


def test_canonical_public_ip_still_allowed():
    # a normal public dotted-quad is fine (not obfuscated, not private)
    assert assert_safe_url("https://93.184.216.34/") == "https://93.184.216.34/"


# ── F2: resolved-IP-after-DNS recheck (sec re-vet finding) ───────────────────


def test_f2_resolved_private_ip_blocked():
    # e.g. 127.0.0.1.nip.io passes the static guard but resolves to loopback
    assert assert_safe_url("https://127.0.0.1.nip.io/") == "https://127.0.0.1.nip.io/"
    with pytest.raises(SSRFError):
        assert_resolved_ips_safe(["127.0.0.1"])
    with pytest.raises(SSRFError):
        assert_resolved_ips_safe(["93.184.216.34", "10.0.0.5"])  # any private fails


def test_f2_resolved_public_ips_ok():
    assert_resolved_ips_safe(["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]) is None


def test_f2_empty_resolution_fails_closed():
    with pytest.raises(SSRFError):
        assert_resolved_ips_safe([])


# ── resolve_and_pin: the unskippable recheck helper (sec suggestion) ─────────


def _fake_getaddrinfo(*addrs):
    """A getaddrinfo-shaped resolver returning the given addresses (for tests)."""
    def _resolver(host, port, *a, **k):
        return [(None, None, None, "", (addr, port)) for addr in addrs]
    return _resolver


def test_resolve_and_pin_returns_vetted_public_ip():
    from research import resolve_and_pin

    pinned = resolve_and_pin("example.com", resolver=_fake_getaddrinfo("93.184.216.34"))
    assert pinned == "93.184.216.34"


def test_resolve_and_pin_blocks_private_resolution():
    from research import resolve_and_pin

    # 127.0.0.1.nip.io-style: resolves to loopback -> must raise before connect
    with pytest.raises(SSRFError):
        resolve_and_pin("evil.nip.io", resolver=_fake_getaddrinfo("127.0.0.1"))
    # any private address among the results fails closed
    with pytest.raises(SSRFError):
        resolve_and_pin("x.com", resolver=_fake_getaddrinfo("93.184.216.34", "10.0.0.5"))


def test_resolve_and_pin_dns_failure_is_ssrf_error():
    from research import resolve_and_pin

    def _boom(*a, **k):
        raise OSError("nxdomain")

    with pytest.raises(SSRFError):
        resolve_and_pin("nope.invalid", resolver=_boom)


def test_firecrawl_fetch_runs_ssrf_guard_before_notimplemented():
    # An unsafe target is rejected by the guard (SSRFError), not reached as a
    # NotImplemented seam — proving the guard runs first.
    with pytest.raises(SSRFError):
        FirecrawlProvider().fetch("https://127.0.0.1/secret")
    # A safe target passes the guard, then hits the (un-wired) live seam.
    with pytest.raises(NotImplementedError):
        FirecrawlProvider().fetch("https://www.reddit.com/r/tattoos/")


# ── official-API-only ────────────────────────────────────────────────────────


def test_official_endpoint_allowlist():
    assert assert_official_endpoint("https://api.firecrawl.dev/v1/scrape", "firecrawl")
    assert assert_official_endpoint("https://api.foreplay.co/v1/ads", "meta_ad_library")
    assert assert_official_endpoint("https://graph.facebook.com/v25.0/ads_archive", "meta_ad_library")
    with pytest.raises(SSRFError):
        assert_official_endpoint("https://evil.example.com/api", "firecrawl")
    with pytest.raises(SSRFError):
        assert_official_endpoint("http://api.firecrawl.dev/x", "firecrawl")  # non-https


def test_exa_and_foreplay_official_endpoints_allowlisted():
    """Regression (gy2): the live exa/foreplay clients must NOT be fail-closed
    SSRF-rejected for lack of an allowlist entry. Before the fix, the 'exa' and
    'foreplay' provider names mapped to an EMPTY allowlist, so their own official
    bases raised SSRFError — blocking the provider the moment the live client wired.
    The provider names match ExaProvider.name / ForeplayProvider.name."""
    assert assert_official_endpoint("https://api.exa.ai/search", "exa")
    assert assert_official_endpoint("https://api.foreplay.co/v1/ads", "foreplay")
    # still fail-closed for a non-official host under each provider name
    with pytest.raises(SSRFError):
        assert_official_endpoint("https://evil.example.com/api", "exa")
    with pytest.raises(SSRFError):
        assert_official_endpoint("https://api.exa.ai/search", "foreplay")  # wrong host for foreplay
    with pytest.raises(SSRFError):
        assert_official_endpoint("http://api.exa.ai/search", "exa")  # non-https


# ── rate limiter ─────────────────────────────────────────────────────────────


def test_rate_limiter_token_bucket():
    rl = RateLimiter(rate=1.0, burst=2)
    assert rl.try_acquire(now=0.0)      # token 1
    assert rl.try_acquire(now=0.0)      # token 2 (burst)
    assert not rl.try_acquire(now=0.0)  # empty
    assert rl.try_acquire(now=1.0)      # +1s -> +1 token
    with pytest.raises(RateLimitError):
        rl.acquire(now=1.0)             # empty again


def test_live_providers_carry_a_limiter():
    assert isinstance(FirecrawlProvider()._limiter, RateLimiter)
    assert isinstance(MetaAdLibraryProvider()._limiter, RateLimiter)
