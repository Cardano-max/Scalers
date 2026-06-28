"""Source providers for the research adapter (bead 1mk.4).

``FixtureProvider`` is deterministic + offline (tests, safe default). The live
providers (``FirecrawlProvider``, ``MetaAdLibraryProvider``) are eng-owned seams
that do official-API, TLS-verified I/O — they raise ``NotImplementedError`` until
wired, and the router degrades to the fixture cleanly.
"""

from research.providers.firecrawl import FirecrawlProvider
from research.providers.fixture import FixtureProvider
from research.providers.meta_ad_library import MetaAdLibraryProvider

__all__ = ["FixtureProvider", "FirecrawlProvider", "MetaAdLibraryProvider"]
