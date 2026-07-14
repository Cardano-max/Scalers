"""Anthropic-powered research provider tests (client-directed PRIMARY) — DB-free,
NO real network.

Mirrors test_exa_search.py / test_firecrawl_search.py: the same shared vetted
boundary asserted with a fake fetcher + fake resolver — gated mock-default,
pin-to-IP (connects to the resolved-and-vetted IP with Host=api.anthropic.com),
key-from-env (x-api-key header), official-API-only — plus the HONESTY GATE: hits
are parsed VERBATIM from real ``web_search_result`` blocks and a
malformed/empty/refused response yields an empty list (never a fabricated
citation). Also pins the Fable-5 request shape: no ``thinking`` field, the
web-search tool, and the server-side fallback to Opus.
"""

from __future__ import annotations

import json

import pytest

from research.adapter import Channel, ResearchQuery
from research.providers._http import HttpResponse, SearchResult
from research.providers.anthropic_research import (
    AnthropicDisabledError,
    AnthropicResearchProvider,
    _extract_web_search_results,
)
from research.safety import SSRFError

# A realistic /v1/messages response: server_tool_use → web_search_tool_result
# (a LIST of real web_search_result hits) → the model's synthesized text.
_MESSAGES_BODY = json.dumps(
    {
        "id": "msg_1",
        "model": "claude-fable-5",
        "stop_reason": "end_turn",
        "content": [
            {"type": "server_tool_use", "id": "srv_1", "name": "web_search",
             "input": {"query": "black and grey realism tattoo top posts"}},
            {
                "type": "web_search_tool_result",
                "tool_use_id": "srv_1",
                "content": [
                    {"type": "web_search_result",
                     "url": "https://www.instagram.com/p/realismking/",
                     "title": "Black & grey realism — 48k likes",
                     "page_age": "3 days ago"},
                    {"type": "web_search_result",
                     "url": "https://skims.com/blog/hook-strategy",
                     "title": "How Skims hooks attention"},
                ],
            },
            {"type": "text", "text": "The winning posts lead with a bold before/after hook."},
        ],
    }
)


class _FakeFetcher:
    def __init__(self, body: str = _MESSAGES_BODY, status: int = 200):
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
    kw.setdefault("api_key", "sk-ant-key")
    kw.setdefault("fetcher", _FakeFetcher())
    kw.setdefault("resolver", _resolver("160.79.104.10"))
    return AnthropicResearchProvider(enabled=True, **kw)


# ── gated mock-default ───────────────────────────────────────────────────────


def test_search_disabled_by_default_refuses():
    with pytest.raises(AnthropicDisabledError):
        AnthropicResearchProvider(api_key="k").search("black and grey realism")


def test_search_missing_key_refuses():
    p = AnthropicResearchProvider(enabled=True, fetcher=_FakeFetcher(),
                                  resolver=_resolver("160.79.104.10"))
    with pytest.raises(AnthropicDisabledError):
        p.search("anything")


def test_search_empty_query_rejected():
    with pytest.raises(ValueError):
        _provider().search("   ")


# ── pin-to-IP + Host + key + official /v1/messages path + Fable-5 shape ──────


def test_default_models_follow_the_cost_policy():
    # 2026-07-14 operator order: the research path DEFAULTS to haiku (both the
    # primary and the server-side fallback); RESEARCH_*_MODEL envs lift it.
    import research.providers.anthropic_research as ar

    assert ar.PRIMARY_MODEL == "claude-haiku-4-5"
    assert ar.FALLBACK_MODEL == "claude-haiku-4-5"


def test_search_connects_to_pinned_ip_with_host_key_and_fable5_payload():
    # The fable/opus ids are passed EXPLICITLY: this test pins the Fable-5
    # payload SHAPE (beta fallback header, no thinking, no sampling params)
    # for when the operator lifts the cost policy — the defaults are haiku.
    fake = _FakeFetcher()
    p = _provider(fetcher=fake, model="claude-fable-5",
                  fallback_model="claude-opus-4-8")
    results = p.search("black and grey realism tattoo top posts", limit=5)
    call = fake.calls[0]
    assert call["ip"] == "160.79.104.10"              # resolved + vetted IP
    assert call["host"] == "api.anthropic.com"        # SNI/Host = official host
    assert call["path"] == "/v1/messages"             # official Messages endpoint
    assert call["headers"]["x-api-key"] == "sk-ant-key"     # key-from-env header
    assert call["headers"]["anthropic-version"] == "2023-06-01"
    # server-side fallback opt-in (Fable-5 guidance): beta header + fallbacks param
    assert call["headers"]["anthropic-beta"] == "server-side-fallback-2026-06-01"
    body = json.loads(call["body"])
    assert body["model"] == "claude-fable-5"
    assert body["fallbacks"] == [{"model": "claude-opus-4-8"}]
    # Fable-5: thinking is always on -> the field must NOT be sent (400 otherwise)
    assert "thinking" not in body
    # no sampling params on Fable 5
    assert "temperature" not in body and "top_p" not in body
    # the official web-search server tool is declared
    assert body["tools"][0]["type"] == "web_search_20260209"
    assert body["tools"][0]["name"] == "web_search"
    # hits parse verbatim from the real web_search_result blocks
    assert [r.url for r in results] == [
        "https://www.instagram.com/p/realismking/",
        "https://skims.com/blog/hook-strategy",
    ]
    assert results[0].snippet == "3 days ago"          # page_age preferred
    assert results[1].snippet == "How Skims hooks attention"  # title fallback


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


def test_fallback_can_be_disabled():
    fake = _FakeFetcher()
    p = _provider(fetcher=fake, fallback_model=None)
    p.search("q")
    body = json.loads(fake.calls[0]["body"])
    assert "fallbacks" not in body
    assert "anthropic-beta" not in fake.calls[0]["headers"]


# ── gather -> typed shapes + raw citable sources ─────────────────────────────


def test_gather_maps_hits_to_web_signals_and_sources():
    p = _provider()
    res = p.gather(ResearchQuery(intent="map_market", niche="black and grey realism",
                                 channels=(Channel.WEB,), limit=4))
    assert res.signals and all(s.channel == Channel.WEB for s in res.signals)
    assert all(s.url for s in res.signals)
    assert res.sources and all(s["url"] for s in res.sources)
    assert {s["url"] for s in res.sources} == {s.url for s in res.signals}


def test_gather_competitor_intent_yields_creatives():
    p = _provider()
    res = p.gather(ResearchQuery(intent="competitor_creatives",
                                 niche="realism tattoo", competitor="realismking",
                                 channels=(Channel.WEB,), limit=4))
    assert res.creatives and all(c.url for c in res.creatives)


def test_gather_disabled_raises_for_router_to_degrade():
    with pytest.raises(AnthropicDisabledError):
        AnthropicResearchProvider().gather(
            ResearchQuery(intent="map_market", niche="tattoo")
        )


# ── HONESTY GATE: never fabricate from a bad/empty/refused response ──────────


@pytest.mark.parametrize(
    "body",
    [
        "", "not json", "{}", '{"content": null}', '{"content": []}',
        # web_search_tool_result whose content is an error object, not a list
        '{"content": [{"type": "web_search_tool_result", "content": {"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"}}]}',
        # a hit with no url is not citable
        '{"content": [{"type": "web_search_tool_result", "content": [{"type": "web_search_result", "title": "no url"}]}]}',
        # the whole fallback chain refused -> honest empty
        '{"stop_reason": "refusal", "content": [{"type": "web_search_tool_result", "content": [{"type": "web_search_result", "url": "https://x.test/a"}]}]}',
    ],
)
def test_extract_never_fabricates(body):
    assert _extract_web_search_results(body, limit=5) == []


def test_extract_is_verbatim():
    out = _extract_web_search_results(_MESSAGES_BODY, limit=5)
    assert all(isinstance(r, SearchResult) for r in out)
    assert out[0].url == "https://www.instagram.com/p/realismking/"
    minimal = _extract_web_search_results(
        '{"content": [{"type": "web_search_tool_result", "content": [{"type": "web_search_result", "url": "https://x.test/a"}]}]}',
        limit=5,
    )
    assert minimal[0].url == "https://x.test/a"
    assert minimal[0].title is None and minimal[0].snippet is None
