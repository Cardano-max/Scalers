"""MetaAdLibraryProvider — competitor ad creatives via Foreplay / Meta Ad Library.

The competitor-pr-finder backend (bead 1mk.4): finds where competitor tattoo
studios advertise, the creatives that run longest, and the **angle** that makes
them work — the ready-to-adapt cold-pitch/positioning seed. Foreplay is the
primary API (richer ad intelligence over the Meta Ad Library); the raw Meta Ad
Library API is the fallback. Official APIs only, TLS verified, no scraping.

STATUS: contract only — ``gather`` raises ``NotImplementedError`` until eng wires
the live client; the router skips + notes, so research keeps working on fixtures.

eng contract:
  * Read the access token from the tenant pack secret (``[secrets.meta_access_token]``
    / ``[secrets.foreplay_api_key]``), never inline.
  * ``gather(query)`` for intent=competitor_creatives -> ProviderResult.creatives:
    map each long-running competitor ad to a Creative(angle, hook, format, url,
    confidence, evidence). Set ``confidence`` from match strength (handle/name +
    locale) so the router can flag likely false positives for operator review.
  * Respect rate limits + ToS; do not exceed the library's documented query caps.
"""

from __future__ import annotations

from research.adapter import Channel, ProviderResult, ResearchQuery
from research.safety import RateLimiter

_META_CHANNELS = frozenset({Channel.META_AD_LIBRARY})
# Official bases — the ONLY hosts this provider may call (TLS): Foreplay (primary)
# + the Meta Ad Library Graph API (fallback). See safety.OFFICIAL_API_HOSTS.
META_API_BASES = ("https://api.foreplay.co", "https://graph.facebook.com")


class MetaAdLibraryProvider:
    """Competitor-creative provider (Foreplay primary). Live client is eng-owned.

    sec hardening (bead 1mk.4): keys come from the pack secret (never inline), the
    only callable bases are the official Foreplay/Meta hosts over TLS, and a
    token-bucket rate limiter gates calls (respect the library's query caps/ToS).
    """

    name = "meta_ad_library"
    channels: frozenset[Channel] = _META_CHANNELS

    def __init__(
        self,
        access_token: str | None = None,
        *,
        foreplay_key: str | None = None,
        rate: float = 1.0,
        burst: int = 3,
    ) -> None:
        # key-from-pack: [secrets.meta_access_token] / [secrets.foreplay_api_key].
        self._access_token = access_token
        self._foreplay_key = foreplay_key
        self._api_bases = META_API_BASES
        self._limiter = RateLimiter(rate=rate, burst=burst)

    def gather(self, query: ResearchQuery) -> ProviderResult:
        raise NotImplementedError(
            "MetaAdLibraryProvider.gather: eng to wire Foreplay (primary) / Meta "
            "Ad Library official API (TLS, key-from-pack, rate-limited) for "
            "competitor creatives. Router uses the fixture provider until then."
        )
