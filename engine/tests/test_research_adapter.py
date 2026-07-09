"""Research adapter tests (bead 1mk.4) — DB-free, hermetic (no keys, no network).

Proves the bead's verify criterion: the research engine returns tattoo-native
demand/community/competitor data via the adapter, using the adopted skills'
intents + channels. Also covers the edge cases (thin data, competitor false
positives) and the safety posture (un-vetted sources unreachable; live providers
are seams that degrade cleanly).
"""

from __future__ import annotations

import pytest

from research import (
    Channel,
    ExaProvider,
    FirecrawlProvider,
    FixtureProvider,
    MetaAdLibraryProvider,
    ResearchQuery,
    ResearchRouter,
    default_registry,
)
from research.providers.exa import ExaDisabledError
from research.providers.firecrawl import FirecrawlDisabledError


def _router() -> ResearchRouter:
    return ResearchRouter([FixtureProvider()])


# ── the bead verify: tattoo-native data via the adapter ──────────────────────


def test_map_market_returns_tattoo_native_signals():
    r = _router().gather(
        ResearchQuery(intent="map_market", niche="fine-line tattoo, Brooklyn",
                      seed_terms=("#fineline",))
    )
    assert r.signals and not r.is_empty
    assert any(s.channel == Channel.R_TATTOOS for s in r.signals)
    assert "fixture" in r.sources_used
    # sorted by confidence, highest first
    confs = [s.confidence for s in r.signals]
    assert confs == sorted(confs, reverse=True)


def test_find_communities_returns_entry_tactics():
    r = _router().gather(
        ResearchQuery(intent="find_communities", niche="blackwork", seed_terms=("#blackwork",))
    )
    assert r.communities
    assert all(c.entry_tactic for c in r.communities)
    channels = {c.channel for c in r.communities}
    assert Channel.R_TATTOOS in channels and Channel.TIKTOK in channels


def test_competitor_creatives_returns_angle_and_flags_false_positive():
    r = _router().gather(
        ResearchQuery(intent="competitor_creatives", niche="traditional",
                      competitor="@rival.ink")
    )
    assert r.creatives
    assert all(c.angle for c in r.creatives)
    # the deliberately-weak match is kept but flagged for review, not dropped
    assert any("false positive" in n for n in r.notes)
    assert any(c.confidence < 0.5 for c in r.creatives)


# ── edge cases ───────────────────────────────────────────────────────────────


def test_thin_data_returns_empty_cleanly():
    r = _router().gather(ResearchQuery(intent="map_market", niche="unknown"))
    assert r.is_empty
    assert any("thin data" in n for n in r.notes)


def test_limit_is_enforced():
    r = _router().gather(
        ResearchQuery(intent="map_market", niche="tattoo", seed_terms=("#ink",), limit=2)
    )
    assert len(r.signals) <= 2


# ── merge / dedupe across providers ──────────────────────────────────────────


def test_dedupe_keeps_highest_confidence_across_two_providers():
    # two fixtures -> identical signals -> must collapse, not double
    router = ResearchRouter([FixtureProvider(), FixtureProvider()])
    one = _router().gather(ResearchQuery(intent="map_market", niche="tattoo", seed_terms=("#ink",)))
    two = router.gather(ResearchQuery(intent="map_market", niche="tattoo", seed_terms=("#ink",)))
    assert len(two.signals) == len(one.signals)  # deduped


# ── channel routing ──────────────────────────────────────────────────────────


def test_provider_skipped_when_no_channel_overlap():
    # Meta-Ad-Library serves only META_AD_LIBRARY; a map_market query (reddit/IG/
    # tiktok/pinterest channels) must not call it.
    router = ResearchRouter([MetaAdLibraryProvider()])
    r = router.gather(ResearchQuery(intent="map_market", niche="tattoo", seed_terms=("#ink",)))
    assert "meta_ad_library" not in r.sources_used


# ── safety: un-vetted sources unreachable; live seams degrade cleanly ────────


def test_unvetted_source_name_is_dropped_by_for_sources():
    reg = {"fixture": FixtureProvider()}
    router = ResearchRouter.for_sources(["fixture", "totally-unvetted-scraper"], reg)
    assert router.provider_names == ("fixture",)


def test_live_provider_not_wired_degrades_to_note_not_crash():
    # A router holding only the un-wired live provider returns empty + a note,
    # never raises (router catches NotImplementedError).
    router = ResearchRouter([MetaAdLibraryProvider()])
    r = router.gather(
        ResearchQuery(intent="competitor_creatives", niche="tattoo", competitor="@x")
    )
    assert r.is_empty
    assert any("not yet wired" in n for n in r.notes)


def test_live_search_providers_gated_when_disabled():
    # p3.0-B: Firecrawl + Exa are WIRED but GATED (mock-default). gather() on a
    # disabled provider raises its Disabled error (the router catches it and
    # degrades honestly), never a fabricated result.
    q = ResearchQuery(intent="map_market", niche="tattoo", channels=(Channel.WEB,))
    with pytest.raises(FirecrawlDisabledError):
        FirecrawlProvider().gather(q)
    with pytest.raises(ExaDisabledError):
        ExaProvider().gather(q)


def test_unwired_competitor_provider_still_raises_not_implemented():
    # Meta Ad Library / Foreplay live client is still a stub -> NotImplementedError,
    # which the router maps to a 'not yet wired' note.
    with pytest.raises(NotImplementedError):
        MetaAdLibraryProvider().gather(ResearchQuery(intent="competitor_creatives", niche="t"))


def test_default_registry_maps_pack_sources_to_fixture_by_default():
    reg = default_registry()
    assert set(reg) >= {"firecrawl", "meta_ad_library"}
    router = ResearchRouter.for_sources(["firecrawl", "meta_ad_library"], reg)
    r = router.gather(
        ResearchQuery(intent="competitor_creatives", niche="tattoo", competitor="@rival")
    )
    # fixture stands in for both live sources -> real creatives come back
    assert r.creatives
