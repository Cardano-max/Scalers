"""FirecrawlProvider — official-API web/social fetch (SEAM FOR ENG, not yet wired).

This is the vetted replacement for the upstream skills' stripped ``fetch.py``
(which disabled TLS and read ``GITHUB_TOKEN``/``.env`` — see
docs/skills/vetting-protocol.md and the registry rows). It fetches the
tattoo-native web/social surface via Firecrawl's **official API**, with **TLS
verification ON**, respecting robots/ToS — official APIs only, no scraping bans,
no credential harvesting.

STATUS: contract only. ``gather`` / ``fetch`` raise ``NotImplementedError`` until
eng wires the live client. The router degrades cleanly (skips + notes) so the
research engine keeps working on the fixture provider meanwhile.

eng contract — implement these against the Firecrawl API:
  * ``fetch(url)`` -> Document(text=..., tls_verified=True, fetched_via="firecrawl").
    MUST use a verified TLS context (never ``ssl._create_unverified_context`` /
    ``CERT_NONE``). Read the key from the tenant pack secret / env, never from a
    vendored ``.env``.
  * ``gather(query)`` -> ProviderResult: for map_market/find_communities, pull
    r/tattoos, Instagram-hashtag, Pinterest, TikTok public surfaces for the
    query's niche/seed terms and shape them into Signal/Community objects.
Keep determinism where it matters: any ranking/summarizing of fetched text runs
in a temp-0 cell downstream, not here — this provider only retrieves.
"""

from __future__ import annotations

from research.adapter import (
    Channel,
    Document,
    ProviderResult,
    ResearchQuery,
)

_FIRECRAWL_CHANNELS = frozenset(
    {Channel.R_TATTOOS, Channel.INSTAGRAM_HASHTAG, Channel.PINTEREST, Channel.TIKTOK, Channel.WEB}
)


class FirecrawlProvider:
    """Official-API web/social provider. Live client is eng-owned (see module doc)."""

    name = "firecrawl"
    channels: frozenset[Channel] = _FIRECRAWL_CHANNELS

    def __init__(self, api_key: str | None = None) -> None:
        # Key comes from the tenant pack secret / env at wiring time — never a
        # vendored .env, never GITHUB_TOKEN harvesting.
        self._api_key = api_key

    def gather(self, query: ResearchQuery) -> ProviderResult:
        raise NotImplementedError(
            "FirecrawlProvider.gather: eng to wire the official Firecrawl API "
            "(TLS-verified). Until then the router uses the fixture provider."
        )

    def fetch(self, url: str) -> Document:
        raise NotImplementedError(
            "FirecrawlProvider.fetch: eng to wire official Firecrawl fetch with "
            "TLS verification ON (replaces the stripped TLS-disabled fetch.py)."
        )
