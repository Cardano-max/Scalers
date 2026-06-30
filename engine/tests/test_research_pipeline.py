"""Router gather() live-path + persistence tests (p3.0-B) — DB-free, no network.

Proves the p3.0-B fill end to end with fake fetchers (no real HTTP) + a capture
persist (no DB):

  * FirecrawlProvider.gather() turns a ResearchQuery into real /v2/search hits ->
    WEB signals + raw citable sources;
  * ResearchRouter fans out across providers, merges, and dedupes the raw sources
    by url into result.sources_cited;
  * gather_and_persist() persists ONLY result.sources_cited (the honesty gate);
  * the honest-degradation path: a keyless/disabled live provider raises -> the
    router records a clear note and degrades, never a fabricated citation.
"""

from __future__ import annotations

import json

from research.adapter import Channel, ResearchQuery
from research.pipeline import gather_and_persist, live_registry
from research.providers._http import HttpResponse
from research.providers.firecrawl import FirecrawlProvider
from research.router import ResearchRouter

_FC_BODY = json.dumps(
    {
        "success": True,
        "data": {
            "web": [
                {"url": "https://a.example.org/one", "title": "One", "description": "first"},
                {"url": "https://b.example.org/two", "title": "Two", "description": "second"},
            ]
        },
    }
)


class _FakeFetcher:
    def __init__(self, body: str, status: int = 200):
        self._body, self._status = body, status

    def request(self, *, method, ip, host, path, headers, body, timeout):
        return HttpResponse(status=self._status, body=self._body)


def _resolver(ip="93.184.216.34"):
    def r(host, port, *a, **k):
        return [(None, None, None, "", (ip, port))]
    return r


def _fc(body=_FC_BODY):
    return FirecrawlProvider(api_key="fc", enabled=True, fetcher=_FakeFetcher(body),
                             resolver=_resolver())


def _capture_persist():
    seen = {}

    def persist(*, run_id, tenant_id, sources, dsn=None):
        seen["run_id"] = run_id
        seen["tenant_id"] = tenant_id
        seen["sources"] = sources
        return [f"id-{i}" for i, _ in enumerate(sources)]

    return persist, seen


# ── firecrawl gather -> real web signals + citable sources ───────────────────


def test_firecrawl_gather_produces_web_signals_and_sources():
    res = _fc().gather(ResearchQuery(intent="map_market", niche="fine line tattoo",
                                     channels=(Channel.WEB,), limit=4))
    assert res.signals and all(s.channel == Channel.WEB and s.url for s in res.signals)
    urls = {s["url"] for s in res.sources}
    assert urls == {"https://a.example.org/one", "https://b.example.org/two"}
    assert all({"query", "url", "title", "snippet"} <= set(s) for s in res.sources)


# ── router fan-out + merge + dedupe of raw sources ───────────────────────────


def test_router_dedupes_sources_across_providers_by_url():
    # two live providers returning an overlapping url -> deduped once in sources_cited
    fc1 = _fc()
    fc2 = FirecrawlProvider(
        api_key="fc", enabled=True, resolver=_resolver(),
        fetcher=_FakeFetcher(json.dumps({"data": {"web": [
            {"url": "https://a.example.org/one", "title": "One-dup", "description": "dup"},
            {"url": "https://c.example.org/three", "title": "Three", "description": "third"},
        ]}})),
    )
    # give the two providers distinct names so the router registry keeps both
    fc2.name = "firecrawl2"
    router = ResearchRouter([fc1, fc2])
    res = router.gather(ResearchQuery(intent="map_market", niche="t",
                                      channels=(Channel.WEB,), limit=10))
    urls = [s["url"] for s in res.sources_cited]
    assert sorted(urls) == [
        "https://a.example.org/one",       # deduped (appears in both)
        "https://b.example.org/two",
        "https://c.example.org/three",
    ]
    assert "firecrawl" in res.sources_used and "firecrawl2" in res.sources_used


# ── gather_and_persist persists ONLY real cited sources ──────────────────────


def test_gather_and_persist_writes_real_sources():
    router = ResearchRouter([_fc()])
    persist, seen = _capture_persist()
    res, ids = gather_and_persist(
        router,
        ResearchQuery(intent="map_market", niche="fine line", channels=(Channel.WEB,), limit=5),
        run_id="run-x", tenant_id="tenant-x",
        persist=persist, ensure=lambda dsn: None,
    )
    assert seen["run_id"] == "run-x" and seen["tenant_id"] == "tenant-x"
    assert {s["url"] for s in seen["sources"]} == {s["url"] for s in res.sources_cited}
    assert len(ids) == len(res.sources_cited) > 0


# ── HONESTY: keyless/disabled live provider degrades, nothing persisted ───────


def test_keyless_live_router_degrades_honestly_and_persists_nothing():
    # No keys -> live_registry arms nothing; both providers raise -> router notes +
    # empty -> gather_and_persist persists NOTHING (no fabricated citation).
    reg = live_registry(env={}, include_fixture=False)
    router = ResearchRouter([reg["firecrawl"], reg["exa"]])
    persist, seen = _capture_persist()
    res, ids = gather_and_persist(
        router,
        ResearchQuery(intent="map_market", niche="tattoo", channels=(Channel.WEB,)),
        run_id="run-empty", tenant_id="tenant-x",
        persist=persist, ensure=lambda dsn: None,
    )
    assert res.sources_cited == ()
    assert ids == []
    assert "sources" not in seen  # persist never called
    assert any("firecrawl" in n and "Disabled" in n for n in res.notes)
    assert any("exa" in n and "Disabled" in n for n in res.notes)


def test_live_registry_arms_providers_when_keys_present():
    reg = live_registry(env={"FIRECRAWL_API_KEY": "fc", "EXA_API_KEY": "ex"})
    assert reg["firecrawl"].enabled is True
    assert reg["exa"].enabled is True
    # absent key -> not armed (honest), not half-on
    reg2 = live_registry(env={"FIRECRAWL_API_KEY": "fc"})
    assert reg2["firecrawl"].enabled is True
    assert reg2["exa"].enabled is False
