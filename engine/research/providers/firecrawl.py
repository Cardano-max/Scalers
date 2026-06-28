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
from research.safety import RateLimiter, assert_safe_url

_FIRECRAWL_CHANNELS = frozenset(
    {Channel.R_TATTOOS, Channel.INSTAGRAM_HASHTAG, Channel.PINTEREST, Channel.TIKTOK, Channel.WEB}
)
# Official Firecrawl API base — the ONLY host this provider may call (TLS).
FIRECRAWL_API_BASE = "https://api.firecrawl.dev"


class FirecrawlProvider:
    """Official-API web/social provider. Live client is eng-owned (see module doc).

    The sec hardening (bead 1mk.4) is enforced HERE so the live client cannot skip
    it: keys come from the pack secret (never a vendored .env), the API base is the
    official https host, every ``fetch(url)`` target passes the SSRF guard, and a
    token-bucket rate limiter gates calls.
    """

    name = "firecrawl"
    channels: frozenset[Channel] = _FIRECRAWL_CHANNELS

    def __init__(self, api_key: str | None = None, *, rate: float = 2.0, burst: int = 5) -> None:
        # key-from-pack: the tenant pack secret / env at wiring time — never a
        # vendored .env, never GITHUB_TOKEN harvesting.
        self._api_key = api_key
        self._api_base = FIRECRAWL_API_BASE
        self._limiter = RateLimiter(rate=rate, burst=burst)

    def gather(self, query: ResearchQuery) -> ProviderResult:
        raise NotImplementedError(
            "FirecrawlProvider.gather: eng to wire the official Firecrawl API at "
            f"{self._api_base} (TLS, key-from-pack, rate-limited). Each target URL "
            "must pass safety.assert_safe_url; the router uses the fixture until then."
        )

    def fetch(self, url: str) -> Document:
        # SSRF guard FIRST: never ask Firecrawl to fetch a private/loopback/
        # metadata/obfuscated-numeric/non-https target (replaces the stripped
        # TLS-disabled fetch.py). Static check only — see the MANDATORY runtime
        # step below.
        assert_safe_url(url)
        raise NotImplementedError(
            "FirecrawlProvider.fetch: eng to wire official Firecrawl fetch (TLS on, "
            "key-from-pack, rate-limited). MANDATORY before connect (sec F2): route "
            "the live request through safety.resolve_and_pin(host) and connect to "
            "the returned IP (host via SNI/Host). That single helper resolves + "
            "re-validates every IP + returns the pinned address, so the recheck "
            "cannot be skipped (defeats DNS-rebinding like 127.0.0.1.nip.io)."
        )
