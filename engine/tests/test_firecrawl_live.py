"""Live Firecrawl secure-fetch tests (bead de6) — DB-free, NO real network.

Asserts the safety wiring with a fake fetcher + fake resolver: gated mock-default,
SSRF guard on the target, pin-to-IP-before-connect (connects to the resolved-and-
vetted IP with Host=api.firecrawl.dev), key-from-pack, official-API-only. The real
PinnedHttpsFetcher byte path is exercised by sec/operator at go-live, not here.
"""

from __future__ import annotations

import json

import pytest

from research.providers.firecrawl import (
    FirecrawlDisabledError,
    FirecrawlProvider,
    HttpResponse,
)
from research.safety import SSRFError


class _FakeFetcher:
    def __init__(self, body: str = '{"data": {"markdown": "hello"}}', status: int = 200):
        self.calls = []
        self._body, self._status = body, status

    def request(self, *, method, ip, host, path, headers, body, timeout):
        self.calls.append({"method": method, "ip": ip, "host": host, "path": path,
                           "headers": headers, "body": body})
        return HttpResponse(status=self._status, body=self._body)


def _resolver(*ips):
    def r(host, port, *a, **k):
        return [(None, None, None, "", (ip, port)) for ip in ips]
    return r


def _provider(**kw):
    kw.setdefault("api_key", "fc-key")
    kw.setdefault("fetcher", _FakeFetcher())
    kw.setdefault("resolver", _resolver("93.184.216.34"))
    return FirecrawlProvider(enabled=True, **kw)


# ── gated mock-default ───────────────────────────────────────────────────────


def test_disabled_by_default_refuses_live_fetch():
    p = FirecrawlProvider(api_key="k")  # enabled defaults False
    assert p.enabled is False
    with pytest.raises(FirecrawlDisabledError):
        p.fetch("https://example.com/")


def test_missing_key_refuses():
    p = FirecrawlProvider(enabled=True, fetcher=_FakeFetcher(), resolver=_resolver("93.184.216.34"))
    with pytest.raises(FirecrawlDisabledError):
        p.fetch("https://example.com/")


# ── SSRF guard runs first (even before the gate) ─────────────────────────────


@pytest.mark.parametrize("bad", ["http://example.com/", "https://127.0.0.1/", "https://2130706433/"])
def test_ssrf_guard_rejects_target(bad):
    with pytest.raises(SSRFError):
        FirecrawlProvider(api_key="k").fetch(bad)


# ── pin-to-IP + Host + key + official path ───────────────────────────────────


def test_connects_to_pinned_ip_with_host_and_key():
    fake = _FakeFetcher()
    p = _provider(fetcher=fake)
    doc = p.fetch("https://www.reddit.com/r/tattoos/")
    call = fake.calls[0]
    assert call["ip"] == "93.184.216.34"         # the resolved + vetted IP
    assert call["host"] == "api.firecrawl.dev"   # SNI/Host = official host
    assert call["path"] == "/v1/scrape"
    assert call["headers"]["Authorization"] == "Bearer fc-key"
    assert json.loads(call["body"])["url"] == "https://www.reddit.com/r/tattoos/"
    assert doc.text == "hello" and doc.fetched_via == "firecrawl"


def test_rebinding_to_private_ip_is_blocked():
    # resolve returns a private IP -> resolve_and_pin raises -> no connect
    p = _provider(resolver=_resolver("10.0.0.5"))
    with pytest.raises(SSRFError):
        p.fetch("https://example.com/")


def test_http_error_status_raises():
    p = _provider(fetcher=_FakeFetcher(body="{}", status=500))
    with pytest.raises(RuntimeError):
        p.fetch("https://example.com/")
