"""a9m.2 research-adapter tests — DB-free, hermetic (no keys, no network).

Maps to the bead AC: MOCK run returns a normalized scored result with ZERO live
calls; a capped run stops at the cap with over_budget=True; a dead backend
degrades (run continues on the others); Foreplay is primary + Meta Ad Library the
fallback; no Reddit backend; live mode never hard-errors.
"""

from __future__ import annotations

from research.adapter import Channel, ResearchQuery
from research.content import (
    Budget,
    Mode,
    ResearchAdapter,
    ScoreBreakdown,
    build_adapter,
    mock_providers,
)
from research.content.mock import MockBackend


def _q(intent="map_market", **kw):
    return ResearchQuery(intent=intent, niche=kw.pop("niche", "fine-line tattoo"), **kw)


class _DeadBackend:
    def __init__(self, name, channels):
        self.name, self.channels = name, channels

    def cost_estimate(self, q):
        return 0.0

    def gather(self, q):
        raise RuntimeError("backend down")


class _CostedMock(MockBackend):
    def __init__(self, name, channels, credits):
        super().__init__(name, channels)
        self._c = credits

    def cost_estimate(self, q):
        return self._c


# ── MOCK run: normalized scored result, zero live calls ──────────────────────


def test_mock_run_returns_scored_items_zero_live_calls():
    adapter = build_adapter()  # default = MOCK
    res = adapter.run(_q(intent="map_market", seed_terms=("#fineline",)))
    assert res.mode is Mode.MOCK
    assert res.items and not res.is_empty
    assert all(0.0 <= i.score <= 1.0 for i in res.items)
    # sorted by score desc; single float score; breakdown reserved (None)
    assert [i.score for i in res.items] == sorted((i.score for i in res.items), reverse=True)
    assert all(i.breakdown is None for i in res.items)
    # zero live calls -> nothing degraded as "not wired", sources are mock backends
    assert not res.degraded
    assert set(res.sources_used) <= {"exa", "firecrawl"}


def test_default_mode_is_mock_and_auto_mock_without_keys():
    assert build_adapter()._mode is Mode.MOCK
    assert build_adapter(keys={})._mode is Mode.MOCK   # empty keys -> MOCK


# ── budget cap: stop at cap, return partial + over_budget ────────────────────


def test_budget_call_cap_returns_partial_over_budget():
    adapter = ResearchAdapter(mock_providers(), budget=Budget(max_calls=1), mode=Mode.MOCK)
    res = adapter.run(_q(intent="map_market", seed_terms=("#ink",)))
    assert res.over_budget is True
    assert len(res.sources_used) == 1            # stopped after the first backend
    assert any("budget cap" in n for n in res.notes)


def test_budget_credit_cap_stops_before_overspend():
    providers = [
        _CostedMock("exa", frozenset({Channel.WEB}), credits=3.0),
        _CostedMock("firecrawl", frozenset({Channel.WEB}), credits=3.0),
    ]
    adapter = ResearchAdapter(providers, budget=Budget(max_credits=4.0), mode=Mode.MOCK)
    res = adapter.run(_q(intent="map_market", seed_terms=("#ink",)))
    # first call (3 credits) fits; second (would be 6 > 4) is refused
    assert res.sources_used == ("exa",)
    assert res.over_budget is True


# ── degradation: a dead backend never sinks the run ──────────────────────────


def test_dead_backend_degrades_run_continues():
    providers = [
        _DeadBackend("exa", frozenset({Channel.WEB})),
        MockBackend("firecrawl", frozenset({Channel.WEB})),
    ]
    res = ResearchAdapter(providers, mode=Mode.MOCK).run(_q(intent="map_market", seed_terms=("#ink",)))
    assert "exa" in res.degraded
    assert "firecrawl" in res.sources_used
    assert res.items                                  # still succeeded on the other


# ── Foreplay primary, Meta Ad Library fallback ───────────────────────────────


def test_foreplay_primary_satisfied_skips_meta_fallback():
    res = ResearchAdapter(mock_providers(), mode=Mode.MOCK).run(
        _q(intent="competitor_creatives", competitor="@rival")
    )
    assert "foreplay" in res.sources_used
    assert "meta_ad_library" not in res.sources_used   # fallback skipped
    assert any("fallback skipped" in n for n in res.notes)
    assert any(i.kind == "competitor_creative" for i in res.items)


def test_meta_fallback_used_when_foreplay_dead():
    providers = [
        _DeadBackend("foreplay", frozenset({Channel.META_AD_LIBRARY})),
        MockBackend("meta_ad_library", frozenset({Channel.META_AD_LIBRARY})),
    ]
    res = ResearchAdapter(providers, mode=Mode.MOCK).run(
        _q(intent="competitor_creatives", competitor="@rival")
    )
    assert res.degraded == ("foreplay",)
    assert "meta_ad_library" in res.sources_used        # fallback fired
    assert any(i.kind == "competitor_creative" for i in res.items)


# ── no Reddit backend (MVP brain) ────────────────────────────────────────────


def test_no_reddit_backend():
    names = build_adapter().provider_names
    assert "reddit" not in names
    assert names == ("exa", "firecrawl", "foreplay", "meta_ad_library")
    # no backend serves the r_tattoos channel
    for p in mock_providers():
        assert Channel.R_TATTOOS not in p.channels


# ── zero results valid; live mode degrades, never crashes ────────────────────


def test_zero_results_is_valid_not_a_crash():
    res = build_adapter().run(_q(intent="map_market", niche="unknown"))
    assert res.is_empty
    assert any("zero results" in n for n in res.notes)


def test_live_mode_degrades_not_crashes_until_wired():
    adapter = build_adapter(mode=Mode.LIVE, keys={"exa": "k", "firecrawl": "k"})
    res = adapter.run(_q(intent="map_market", seed_terms=("#ink",)))
    assert res.mode is Mode.LIVE
    assert res.is_empty                                 # seams raise -> degrade
    assert set(res.degraded) >= {"exa", "firecrawl"}
    assert res.over_budget is False                     # not a budget stop, a degrade


def test_score_breakdown_reserved_shape():
    sb = ScoreBreakdown()
    assert sb.relevance is None and sb.recency is None and sb.authority is None
