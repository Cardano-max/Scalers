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
