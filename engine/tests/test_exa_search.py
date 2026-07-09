"""Live Exa secure-SEARCH tests (p3.0-B) — DB-free, NO real network.

Mirrors test_firecrawl_search.py for the Exa ``POST /search`` path: same shared
vetted boundary asserted with a fake fetcher + fake resolver — gated mock-default,
pin-to-IP (connects to the resolved-and-vetted IP with Host=api.exa.ai),
key-from-env (x-api-key header), official-API-only — plus the HONESTY GATE:
results are parsed verbatim from the response and a malformed/empty response
yields an empty list (never a fabricated citation). No EXA_API_KEY is provisioned
in the env yet, so this fake-fetcher suite is the proof the live path is real code.
"""

from __future__ import annotations

import json

import pytest

from research.adapter import Channel, ResearchQuery
from research.providers._http import HttpResponse, SearchResult
from research.providers.exa import ExaDisabledError, ExaProvider, _extract_exa_results
from research.safety import SSRFError

_SEARCH_BODY = json.dumps(
    {
        "requestId": "req-1",
        "results": [
            {
                "url": "https://www.tattoodo.com/a/fine-line-tattoo-trends-2026",
                "title": "Fine Line Tattoo Trends",
                "highlights": ["Fine line micro-realism is surging in 2026."],
                "score": 0.41,
            },
            {
                "url": "https://inkedmag.com/fine-line-aftercare",
                "title": "Fine Line Aftercare",
                "text": "Keep it moisturized and out of the sun for the first two weeks.",
            },
        ],
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
    kw.setdefault("api_key", "exa-key")
    kw.setdefault("fetcher", _FakeFetcher())
    kw.setdefault("resolver", _resolver("93.184.216.34"))
    return ExaProvider(enabled=True, **kw)


# ── gated mock-default ───────────────────────────────────────────────────────


def test_search_disabled_by_default_refuses():
    with pytest.raises(ExaDisabledError):
        ExaProvider(api_key="k").search("fine line tattoo trends")


def test_search_missing_key_refuses():
    p = ExaProvider(enabled=True, fetcher=_FakeFetcher(), resolver=_resolver("93.184.216.34"))
    with pytest.raises(ExaDisabledError):
        p.search("anything")


def test_search_empty_query_rejected():
    with pytest.raises(ValueError):
        _provider().search("   ")


# ── pin-to-IP + Host + key + official /search path ───────────────────────────


def test_search_connects_to_pinned_ip_with_host_key_and_path():
    fake = _FakeFetcher()
    p = _provider(fetcher=fake)
    results = p.search("fine line tattoo trends", limit=5)
    call = fake.calls[0]
    assert call["ip"] == "93.184.216.34"        # resolved + vetted IP
    assert call["host"] == "api.exa.ai"         # SNI/Host = official host
    assert call["path"] == "/search"            # official Exa search endpoint
    assert call["headers"]["x-api-key"] == "exa-key"   # key-from-env header
    body = json.loads(call["body"])
    assert body["query"] == "fine line tattoo trends"
    assert body["numResults"] == 5
    assert body["type"] == "auto"               # neural/deep where it serves best
    # results parse verbatim from the response
    assert [r.url for r in results] == [
        "https://www.tattoodo.com/a/fine-line-tattoo-trends-2026",
        "https://inkedmag.com/fine-line-aftercare",
    ]
    assert results[0].snippet == "Fine line micro-realism is surging in 2026."
    # snippet falls back to a real text prefix when there are no highlights
    assert results[1].snippet.startswith("Keep it moisturized")


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


# ── gather -> WEB signals + raw citable sources ──────────────────────────────


def test_gather_maps_hits_to_web_signals_and_sources():
    p = _provider()
    res = p.gather(ResearchQuery(intent="map_market", niche="fine line tattoo",
                                 channels=(Channel.WEB,), limit=4))
    assert res.signals and all(s.channel == Channel.WEB for s in res.signals)
    assert all(s.url for s in res.signals)
    # raw citable sources carry verbatim {query,url,title,snippet} for persistence
    assert res.sources and all(s["url"] for s in res.sources)
    assert {s["url"] for s in res.sources} == {s.url for s in res.signals}


def test_gather_disabled_raises_for_router_to_degrade():
    with pytest.raises(ExaDisabledError):
        ExaProvider().gather(ResearchQuery(intent="map_market", niche="tattoo"))


# ── HONESTY GATE: never fabricate from a bad/empty response ──────────────────


@pytest.mark.parametrize(
    "body",
    [
        "", "not json", "{}", '{"results": null}', '{"results": []}',
        '{"results": [{"title": "no url"}]}',
    ],
)
def test_extract_exa_results_never_fabricates(body):
    assert _extract_exa_results(body, limit=5) == []


def test_extract_exa_results_is_verbatim():
    out = _extract_exa_results(_SEARCH_BODY, limit=5)
    assert all(isinstance(r, SearchResult) for r in out)
    assert out[0].url == "https://www.tattoodo.com/a/fine-line-tattoo-trends-2026"
    minimal = _extract_exa_results('{"results": [{"url": "https://x.test/a"}]}', limit=5)
    assert minimal[0].url == "https://x.test/a"
    assert minimal[0].title is None and minimal[0].snippet is None
