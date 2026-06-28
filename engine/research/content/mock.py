"""MOCK / recorded backends + the adapter factory (bead a9m.2, contract §4).

MOCK is the default mode: deterministic fixtures, **zero live calls**, so the
content slice + CI + the dev box run offline. Each mock backend reuses the 1mk.4
``FixtureProvider`` logic under a Phase-3 backend name (exa / firecrawl / foreplay
/ meta_ad_library — **no Reddit backend**), at **zero credit cost**.

``build_adapter`` auto-selects MOCK when no API keys are present, so a missing
secret never hard-errors the build; LIVE wires the real seams (which raise until
eng implements them — LIVE then *degrades* cleanly, it does not crash).
"""

from __future__ import annotations

from collections.abc import Callable

from research.adapter import Channel, ProviderResult, ResearchQuery
from research.content.adapter import ResearchAdapter
from research.content.budget import Budget
from research.content.items import Mode
from research.providers.exa import ExaProvider
from research.providers.firecrawl import FirecrawlProvider
from research.providers.fixture import FixtureProvider
from research.providers.foreplay import ForeplayProvider
from research.providers.meta_ad_library import MetaAdLibraryProvider

# Phase-3 backend names (Reddit OUT — not in this set).
_WEB = frozenset({Channel.WEB})
_COMPETITOR = frozenset({Channel.META_AD_LIBRARY})


class MockBackend:
    """A named, free, deterministic backend over the FixtureProvider (no network)."""

    def __init__(self, name: str, channels: frozenset[Channel]) -> None:
        self.name = name
        self.channels = channels
        self._fixture = FixtureProvider()

    def cost_estimate(self, query: ResearchQuery) -> float:
        return 0.0  # fixtures are free + offline

    def gather(self, query: ResearchQuery) -> ProviderResult:
        return self._fixture.gather(query)


def mock_providers() -> list[MockBackend]:
    """The default MOCK backend set: Exa + Firecrawl (web) + Foreplay + Meta Ad
    Library (competitor). No Reddit backend."""
    return [
        MockBackend("exa", _WEB),
        MockBackend("firecrawl", _WEB),
        MockBackend("foreplay", _COMPETITOR),
        MockBackend("meta_ad_library", _COMPETITOR),
    ]


def live_providers(keys: dict[str, str] | None = None) -> list:
    """The LIVE seam set (eng-owned; each raises until wired, so LIVE degrades
    cleanly). Keys come from the tenant pack secret / env, never a vendored .env."""
    keys = keys or {}
    return [
        ExaProvider(keys.get("exa")),
        FirecrawlProvider(keys.get("firecrawl")),
        ForeplayProvider(keys.get("foreplay")),
        MetaAdLibraryProvider(keys.get("meta_access_token"), foreplay_key=keys.get("foreplay")),
    ]


def build_adapter(
    *,
    mode: Mode | None = None,
    budget: Budget | None = None,
    keys: dict[str, str] | None = None,
    clock: Callable[[], float] | None = None,
) -> ResearchAdapter:
    """Construct a research adapter. MOCK by default; auto-MOCK when ``keys`` is
    empty (secrets absent → CI/dev never hard-errors). LIVE requires keys + the
    sec go-live gate on each provider."""
    resolved = mode or (Mode.LIVE if keys else Mode.MOCK)
    providers = mock_providers() if resolved is Mode.MOCK else live_providers(keys)
    return ResearchAdapter(providers, budget=budget or Budget.unlimited(), mode=resolved, clock=clock)
