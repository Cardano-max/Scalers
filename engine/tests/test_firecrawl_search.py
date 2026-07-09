"""Live Firecrawl secure-SEARCH tests (slice 3) — DB-free, NO real network.

Mirrors test_firecrawl_live.py for the new ``search()`` path: asserts the same
safety wiring with a fake fetcher + fake resolver — gated mock-default, pin-to-IP
(connects to the resolved-and-vetted IP with Host=api.firecrawl.dev), key-from-pack,
official-API-only, POSTs the query to /v1/search — plus the HONESTY GATE: results
are parsed verbatim from the response and a malformed/empty response yields an
empty list (never a fabricated citation). The real byte path is exercised live.
"""

from __future__ import annotations

import json

import pytest

from research.providers.firecrawl import (
    FirecrawlDisabledError,
    FirecrawlProvider,
    HttpResponse,
    SearchResult,
    _extract_search_results,
)
from research.safety import SSRFError

_SEARCH_BODY = json.dumps(
    {
        "success": True,
        "data": [
            {
                "url": "https://www.bestwishestattoo-la.com/blog/fine-line-aftercare",
                "title": "Fine Line Tattoo Aftercare & Healing Guide",
                "description": "Moisturize regularly and avoid direct sunlight while healing.",
            },
            {
                "url": "https://iglatattoo.com/how-to-heal-a-fine-line-tattoo/",
                "title": "A Week-by-Week Healing Guide",
                "description": "Tapping gently around the area helps without disturbing healing.",
            },
        ],
        "id": "abc-123",
    }
)


class _FakeFetcher:
    def __init__(self, body: str = _SEARCH_BODY, status: int = 200):
        self.calls = []
        self._body, self._status = body, status

    def request(self, *, method, ip, host, path, headers, body, timeout):
        self.calls.append(
            {"method": method, "ip": ip, "host": host, "path": path,
             "headers": headers, "body": body}
        )
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


def test_search_disabled_by_default_refuses():
    p = FirecrawlProvider(api_key="k")  # enabled defaults False
    with pytest.raises(FirecrawlDisabledError):
        p.search("fine line tattoo aftercare")


def test_search_missing_key_refuses():
    p = FirecrawlProvider(enabled=True, fetcher=_FakeFetcher(), resolver=_resolver("93.184.216.34"))
    with pytest.raises(FirecrawlDisabledError):
        p.search("anything")


def test_search_empty_query_rejected():
    with pytest.raises(ValueError):
        _provider().search("   ")


# ── pin-to-IP + Host + key + official /v1/search path ────────────────────────


def test_search_connects_to_pinned_ip_with_host_key_and_path():
    fake = _FakeFetcher()
    p = _provider(fetcher=fake)
    results = p.search("fine line tattoo aftercare", limit=5)
    call = fake.calls[0]
    assert call["ip"] == "93.184.216.34"          # resolved + vetted IP
    assert call["host"] == "api.firecrawl.dev"    # SNI/Host = official host
    assert call["path"] == "/v2/search"           # the official v2 search endpoint
    assert call["headers"]["Authorization"] == "Bearer fc-key"
    body = json.loads(call["body"])
    assert body["query"] == "fine line tattoo aftercare"
    assert body["limit"] == 5
    # results parse verbatim from the response
    assert [r.url for r in results] == [
        "https://www.bestwishestattoo-la.com/blog/fine-line-aftercare",
        "https://iglatattoo.com/how-to-heal-a-fine-line-tattoo/",
    ]
    assert results[0].title == "Fine Line Tattoo Aftercare & Healing Guide"
    assert "Moisturize" in results[0].snippet


def test_search_rebinding_to_private_ip_is_blocked():
    p = _provider(resolver=_resolver("10.0.0.5"))
    with pytest.raises(SSRFError):
        p.search("anything")


def test_search_http_error_status_raises():
    p = _provider(fetcher=_FakeFetcher(body="{}", status=429))
    with pytest.raises(RuntimeError):
        p.search("anything")


def test_search_respects_limit():
    p = _provider()
    assert len(p.search("q", limit=1)) == 1


# ── HONESTY GATE: never fabricate a result from a bad/empty response ─────────


@pytest.mark.parametrize(
    "body",
    [
        "",                              # not JSON
        "not json",                      # garbage
        "{}",                            # no data key
        '{"data": null}',                # null data
        '{"data": []}',                  # empty data
        '{"data": [{"title": "no url"}]}',  # hit without a URL -> dropped
    ],
)
def test_extract_search_results_never_fabricates(body):
    assert _extract_search_results(body, limit=5) == []


def test_extract_search_results_is_verbatim():
    out = _extract_search_results(_SEARCH_BODY, limit=5)
    assert all(isinstance(r, SearchResult) for r in out)
    assert out[0].url == "https://www.bestwishestattoo-la.com/blog/fine-line-aftercare"
    # snippet falls back from description; missing fields stay None, never invented
    minimal = _extract_search_results('{"data": [{"url": "https://x.test/a"}]}', limit=5)
    assert minimal[0].url == "https://x.test/a"
    assert minimal[0].title is None and minimal[0].snippet is None


# ── v2 response shape: data is {web:[...], news:[...]} (the REAL /v2/search) ──

_V2_BODY = json.dumps(
    {
        "success": True,
        "data": {
            "web": [
                {
                    "url": "https://www.bestwishestattoo-la.com/blog/fine-line-aftercare-healing",
                    "title": "Fine Line Tattoo Aftercare & Healing Guide",
                    "description": "Fine line tattoos usually take 5-6 weeks to heal.",
                    "position": 1,
                },
                {
                    "url": "https://iglatattoo.com/how-to-heal-a-fine-line-tattoo/",
                    "title": "A Week-by-Week Healing Guide",
                    "description": "Tap gently around the area while it heals.",
                    "position": 2,
                },
            ]
        },
        "creditsUsed": 2,
        "id": "abc-123",
    }
)


def test_extract_search_results_parses_v2_web_shape():
    out = _extract_search_results(_V2_BODY, limit=5)
    assert [r.url for r in out] == [
        "https://www.bestwishestattoo-la.com/blog/fine-line-aftercare-healing",
        "https://iglatattoo.com/how-to-heal-a-fine-line-tattoo/",
    ]
    assert out[0].title == "Fine Line Tattoo Aftercare & Healing Guide"
    assert "5-6 weeks" in out[0].snippet


def test_search_parses_v2_response_end_to_end():
    p = _provider(fetcher=_FakeFetcher(body=_V2_BODY))
    results = p.search("fine line tattoo aftercare", limit=5)
    assert results[0].url.endswith("fine-line-aftercare-healing")


@pytest.mark.parametrize(
    "body",
    ['{"data": {"web": null}}', '{"data": {}}', '{"data": {"web": []}}'],
)
def test_v2_empty_web_never_fabricates(body):
    assert _extract_search_results(body, limit=5) == []
