"""Research adapter (bead 1mk.4) — the vetted seam behind the research skills.

The three adopted research skills (map-your-market, where-your-customer-lives,
competitor-pr-finder) are prompt-only; ALL network goes through a vetted
:class:`SourceProvider` here (Firecrawl + Foreplay/Meta-Ad-Library, official
APIs, TLS on). The upstream TLS-disabled ``fetch.py`` is stripped, never vendored.

``default_registry()`` is the vetted provider set the router may use. Live
providers are present but raise until eng wires them; the fixture provider keeps
the engine working offline meanwhile.
"""

from research.adapter import (
    Channel,
    Community,
    Creative,
    Document,
    Intent,
    ProviderResult,
    ResearchQuery,
    ResearchResult,
    Signal,
    SourceProvider,
    UnsupportedIntent,
)
from research.providers import FirecrawlProvider, FixtureProvider, MetaAdLibraryProvider
from research.router import ResearchRouter
from research.safety import (
    OFFICIAL_API_HOSTS,
    RateLimiter,
    RateLimitError,
    SSRFError,
    assert_official_endpoint,
    assert_safe_url,
)


def default_registry(*, use_fixture: bool = True) -> dict[str, SourceProvider]:
    """The vetted provider registry, keyed by pack ``[research].sources`` name.

    ``use_fixture=True`` (default) maps the live source names to the deterministic
    fixture so the engine runs end-to-end before eng lands the live clients. Flip
    to ``False`` once the live providers are wired to use real APIs.
    """
    if use_fixture:
        fx = FixtureProvider()
        return {"firecrawl": fx, "meta_ad_library": fx, "fixture": fx}
    return {
        "firecrawl": FirecrawlProvider(),
        "meta_ad_library": MetaAdLibraryProvider(),
        "fixture": FixtureProvider(),
    }


__all__ = [
    "Channel",
    "Community",
    "Creative",
    "Document",
    "Intent",
    "ProviderResult",
    "ResearchQuery",
    "ResearchResult",
    "Signal",
    "SourceProvider",
    "UnsupportedIntent",
    "ResearchRouter",
    "FixtureProvider",
    "FirecrawlProvider",
    "MetaAdLibraryProvider",
    "default_registry",
    "assert_safe_url",
    "assert_official_endpoint",
    "SSRFError",
    "RateLimiter",
    "RateLimitError",
    "OFFICIAL_API_HOSTS",
]
