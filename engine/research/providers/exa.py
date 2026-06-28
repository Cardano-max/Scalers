"""ExaProvider — official-API semantic search/discovery (SEAM FOR ENG, a9m.2).

New backend for the Phase-3 content brain: Exa semantic search for web discovery
(trends, winning patterns, pain signals). Like the other live providers it carries
the sec go-live gate: official API over TLS, key-from-pack, SSRF-guarded fetch,
rate-limited. ``gather`` raises until eng wires the client; in MOCK mode the
adapter uses the mock backend instead, so the slice runs with zero live calls.
"""

from __future__ import annotations

from research.adapter import Channel, ProviderResult, ResearchQuery
from research.safety import RateLimiter

EXA_API_BASE = "https://api.exa.ai"
_EXA_CHANNELS = frozenset({Channel.WEB})


class ExaProvider:
    """Official-API semantic search. Live client is eng-owned (sec go-live gate)."""

    name = "exa"
    channels: frozenset[Channel] = _EXA_CHANNELS

    def __init__(self, api_key: str | None = None, *, rate: float = 2.0, burst: int = 5,
                 credits_per_call: float = 1.0) -> None:
        self._api_key = api_key            # key-from-pack; never a vendored .env
        self._api_base = EXA_API_BASE
        self._limiter = RateLimiter(rate=rate, burst=burst)
        self._credits = credits_per_call

    def cost_estimate(self, query: ResearchQuery) -> float:
        return self._credits

    def gather(self, query: ResearchQuery) -> ProviderResult:
        raise NotImplementedError(
            "ExaProvider.gather: eng to wire the official Exa API at "
            f"{self._api_base} (TLS, key-from-pack, rate-limited). MOCK mode uses "
            "the mock backend until then."
        )
