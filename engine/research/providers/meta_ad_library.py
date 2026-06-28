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

_META_CHANNELS = frozenset({Channel.META_AD_LIBRARY})


class MetaAdLibraryProvider:
    """Competitor-creative provider (Foreplay primary). Live client is eng-owned."""

    name = "meta_ad_library"
    channels: frozenset[Channel] = _META_CHANNELS

    def __init__(self, access_token: str | None = None, *, foreplay_key: str | None = None) -> None:
        self._access_token = access_token
        self._foreplay_key = foreplay_key

    def gather(self, query: ResearchQuery) -> ProviderResult:
        raise NotImplementedError(
            "MetaAdLibraryProvider.gather: eng to wire Foreplay (primary) / Meta "
            "Ad Library official API for competitor creatives. Router uses the "
            "fixture provider until then."
        )
