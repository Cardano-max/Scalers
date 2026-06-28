"""ForeplayProvider — PRIMARY competitor-ad backend (SEAM FOR ENG, a9m.2).

Foreplay Competitor Advertising API is the **primary** competitor-creative source
(operator has access, ~10k free credits/mo); the free Meta Ad Library
(``MetaAdLibraryProvider``) is the FALLBACK the adapter only spends if Foreplay
degrades or returns nothing. Carries the sec go-live gate (official API/TLS,
key-from-pack, rate-limited). ``gather`` raises until eng wires it; MOCK uses the
mock backend.
"""

from __future__ import annotations

from research.adapter import Channel, ProviderResult, ResearchQuery
from research.safety import RateLimiter

FOREPLAY_API_BASE = "https://api.foreplay.co"
_FOREPLAY_CHANNELS = frozenset({Channel.META_AD_LIBRARY})


class ForeplayProvider:
    """Primary competitor-ad provider. Live client is eng-owned (sec go-live gate)."""

    name = "foreplay"
    channels: frozenset[Channel] = _FOREPLAY_CHANNELS

    def __init__(self, api_key: str | None = None, *, rate: float = 1.0, burst: int = 3,
                 credits_per_call: float = 1.0) -> None:
        self._api_key = api_key            # key-from-pack ([secrets.foreplay_api_key])
        self._api_base = FOREPLAY_API_BASE
        self._limiter = RateLimiter(rate=rate, burst=burst)
        self._credits = credits_per_call

    def cost_estimate(self, query: ResearchQuery) -> float:
        return self._credits

    def gather(self, query: ResearchQuery) -> ProviderResult:
        raise NotImplementedError(
            "ForeplayProvider.gather: eng to wire the official Foreplay Competitor "
            f"Advertising API at {self._api_base} (TLS, key-from-pack, rate-limited). "
            "MOCK mode uses the mock backend; Meta Ad Library is the fallback."
        )
