"""AnthropicResearchProvider — Anthropic-powered web research (client-directed primary).

The client's direction (PA meeting, 2026-07-11): move research off the free
Firecrawl path onto Anthropic — Claude ``claude-fable-5`` for the hardest
strategy, with a server-side fallback to ``claude-opus-4-8`` on a policy decline.
This provider makes ONE non-streaming ``POST /v1/messages`` per search string with
the official server-side **web search** tool (``web_search_20260209``), through the
SAME vetted egress boundary the other live providers use
(:func:`research.providers._http.secure_post_json`): official-API-only host
allowlist (``api.anthropic.com``), the F2 resolve-and-pin SSRF recheck,
rate-limited, key-from-env/pack — **disabled by default** (mock-default); no live
call unless ``enabled=True`` + a key.

Fable-5 API specifics baked in (per the Anthropic API reference):
  * thinking is always on — the ``thinking`` field is omitted entirely;
  * no sampling params;
  * ``fallbacks=[{"model": "claude-opus-4-8"}]`` + beta
    ``server-side-fallback-2026-06-01`` so a safety decline is re-served in the
    same call; a final ``stop_reason == "refusal"`` means the whole chain
    declined and we degrade honestly.

HONESTY GATE: every url/title/snippet is copied VERBATIM from a real
``web_search_result`` block in the response — never the model's prose, never a
fabricated citation. No key / disabled / empty search / refusal -> the router
degrades honestly (note + empty). The model's synthesized ``text`` is kept only as
a run note, never promoted to a citable source.
"""

from __future__ import annotations

import json
import os
from typing import Any

from research.adapter import (
    Channel,
    Community,
    Creative,
    ProviderResult,
    ResearchQuery,
    Signal,
)
from research.providers._http import (
    HttpFetcher,
    PinnedHttpsFetcher,
    SearchResult,
    secure_post_json,
)
from research.safety import RateLimiter

ANTHROPIC_API_BASE = "https://api.anthropic.com"
_API_HOST = "api.anthropic.com"
_MESSAGES_PATH = "/v1/messages"

# Model policy (operator order 2026-07-14: "haiku 4.5 all the places, not
# bigger model, to avoid API cost"): the research path now DEFAULTS to haiku,
# with the same server-side fallback shape kept. Both stay ENV-OVERRIDABLE —
# set RESEARCH_PRIMARY_MODEL / RESEARCH_FALLBACK_MODEL to dial the research
# path back up (e.g. to claude-fable-5 per the PA-meeting stack decision)
# without a code change, when the operator lifts the cost order.
PRIMARY_MODEL = os.environ.get("RESEARCH_PRIMARY_MODEL", "claude-haiku-4-5")
FALLBACK_MODEL = os.environ.get("RESEARCH_FALLBACK_MODEL", "claude-haiku-4-5")

# A NON-STREAMING messages call that runs server-side web search takes minutes, not
# seconds — the tool loops several searches before the final text. 120s produced
# real read-timeouts on the very first live call, so the default is generous and
# env-overridable (the operator's knob, like the model ids above).
DEFAULT_TIMEOUT_S = float(os.environ.get("RESEARCH_HTTP_TIMEOUT_S", "300"))

# web search + server-side fallback are both beta surfaces on the Messages API.
_SERVER_SIDE_FALLBACK_BETA = "server-side-fallback-2026-06-01"
_WEB_SEARCH_TOOL = "web_search_20260209"

# Anthropic web research answers the WEB channel, but we register the provider
# against a broad channel set so the router reaches it for every research intent
# (map_market / find_communities / competitor_creatives) — it is the PRIMARY
# provider, not a WEB-only add-on. Every returned hit is still honestly a WEB hit.
_ANTHROPIC_CHANNELS = frozenset(
    {
        Channel.WEB,
        Channel.R_TATTOOS,
        Channel.INSTAGRAM_HASHTAG,
        Channel.PINTEREST,
        Channel.TIKTOK,
        Channel.META_AD_LIBRARY,
    }
)
_WEB = Channel.WEB


class AnthropicDisabledError(RuntimeError):
    """A live Anthropic research call was attempted while disabled (mock-default /
    no key). The safe default; never a silent live call."""


class AnthropicResearchProvider:
    """Claude web-search research. Live client is GATED (disabled by default)."""

    name = "anthropic"
    channels: frozenset[Channel] = _ANTHROPIC_CHANNELS

    def __init__(
        self,
        api_key: str | None = None,
        *,
        enabled: bool = False,
        fetcher: HttpFetcher | None = None,
        model: str = PRIMARY_MODEL,
        fallback_model: str | None = FALLBACK_MODEL,
        max_search_uses: int = 5,
        max_tokens: int = 4096,
        rate: float = 1.0,
        burst: int = 3,
        timeout: float = DEFAULT_TIMEOUT_S,
        clock=None,
        resolver=None,
    ) -> None:
        self._api_key = api_key            # key-from-env/pack; never a vendored .env
        self._api_base = ANTHROPIC_API_BASE
        self._enabled = enabled            # mock-default: no live call unless True
        self._fetcher = fetcher or PinnedHttpsFetcher()
        self._model = model
        self._fallback_model = fallback_model
        self._max_search_uses = max(1, int(max_search_uses))
        self._max_tokens = max(256, int(max_tokens))
        self._limiter = RateLimiter(rate=rate, burst=burst)
        self._timeout = timeout
        self._resolver = resolver
        import time

        self._clock = clock or time.monotonic

    @property
    def enabled(self) -> bool:
        return self._enabled

    # -- one real /v1/messages call with the web-search server tool ---------- #
    def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        """Run ONE real web-search research turn via Claude and return the real,
        citable hits (url/title/snippet) — verbatim from the ``web_search_result``
        blocks. HONESTY GATE: an empty/odd/refused response yields ``[]`` — never
        an invented citation."""
        if not self._enabled:
            raise AnthropicDisabledError(
                "Anthropic live research is disabled (mock-default). Enable only "
                "after sec vet + operator go-live + a provisioned ANTHROPIC_API_KEY."
            )
        if not self._api_key:
            raise AnthropicDisabledError("no Anthropic API key (key-from-env/pack required)")
        if not (query and query.strip()):
            raise ValueError("empty search query")

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "tools": [
                {
                    "type": _WEB_SEARCH_TOOL,
                    "name": "web_search",
                    "max_uses": self._max_search_uses,
                }
            ],
            "messages": [{"role": "user", "content": _research_instruction(query.strip())}],
        }
        # Opt into the server-side fallback so a false-positive decline on benign
        # research is re-served by the fallback model in the same call (Fable-5
        # guidance). A fallback equal to the primary (the haiku-everywhere cost
        # default) is a no-op — skip the opt-in rather than fall back to itself.
        if self._fallback_model and self._fallback_model != self._model:
            headers["anthropic-beta"] = _SERVER_SIDE_FALLBACK_BETA
            payload["fallbacks"] = [{"model": self._fallback_model}]

        resp = secure_post_json(
            fetcher=self._fetcher,
            provider_name="anthropic",
            api_base=self._api_base,
            api_host=_API_HOST,
            path=_MESSAGES_PATH,
            headers=headers,
            payload=payload,
            limiter=self._limiter,
            clock=self._clock,
            timeout=self._timeout,
            resolver=self._resolver,
        )
        return _extract_web_search_results(resp.body, limit=int(limit))

    def gather(self, query: ResearchQuery) -> ProviderResult:
        """Run REAL Claude web research and normalize to the router's shapes.

        Every hit becomes a typed Signal/Community/Creative (by intent) plus a raw
        ``{query,url,title,snippet}`` citable source the router dedupes + persists.
        Disabled/no-key raises (router notes it and degrades); an empty/refused
        search yields an honestly-noted empty result — never a fabricated source.
        """
        if not self._enabled:
            raise AnthropicDisabledError("Anthropic live research is disabled (mock-default); router degrades.")
        if not self._api_key:
            raise AnthropicDisabledError("no Anthropic API key (key-from-env/pack required)")

        terms = _query_strings(query)
        per_query = max(1, query.limit // max(1, len(terms)))
        raw_sources: list[dict[str, Any]] = []
        notes: list[str] = []
        seen: set[str] = set()
        for term in terms:
            try:
                hits = self.search(term, limit=per_query)
            except Exception as exc:  # noqa: BLE001 — record the real reason, keep going
                notes.append(f"anthropic research failed for {term!r}: {type(exc).__name__}: {exc}")
                continue
            for h in hits:
                if h.url in seen:
                    continue
                seen.add(h.url)
                raw_sources.append(
                    {"query": term, "url": h.url, "title": h.title, "snippet": h.snippet}
                )

        if not raw_sources:
            notes.append("anthropic: web research returned no usable sources (honest-empty)")
            return ProviderResult(notes=tuple(notes))

        signals: list[Signal] = []
        communities: list[Community] = []
        creatives: list[Creative] = []
        for rank, s in enumerate(raw_sources):
            # ``conf`` is a transparent POSITIONAL search-rank prior (earlier hit →
            # higher), NOT a measured confidence — it only orders results; the real
            # ground truth is the verbatim url/title/snippet. ``kind="demand"`` below
            # is the honest default for the map_market intent (generic web results are
            # demand-side market signals); pain/angle need intent-aware LLM
            # classification, deliberately not added here to keep this a single
            # search call with no extra model spend.
            conf = max(0.4, round(0.85 - 0.05 * rank, 2))
            text = s["snippet"] or s["title"] or s["url"]
            if query.intent == "find_communities":
                communities.append(
                    Community(
                        name=s["title"] or s["url"], channel=_WEB,
                        entry_tactic="engage authentically on this source; add value first",
                        url=s["url"], size_hint=None,
                    )
                )
            elif query.intent == "competitor_creatives":
                creatives.append(
                    Creative(
                        competitor=query.competitor or query.niche, channel=_WEB,
                        angle=s["title"] or text, hook=None, format=None,
                        url=s["url"], confidence=conf, evidence=(s["query"],),
                    )
                )
            else:
                signals.append(
                    Signal(
                        text=text, channel=_WEB, kind="demand", confidence=conf,
                        url=s["url"], evidence=(s["query"],),
                    )
                )
        return ProviderResult(
            signals=tuple(signals),
            communities=tuple(communities),
            creatives=tuple(creatives),
            sources=tuple(raw_sources),
            notes=tuple(notes),
        )


def _research_instruction(query: str) -> str:
    """The research turn's user message. Instructs Claude to search the live web
    and ground everything in real sources — the citations we extract come from the
    tool's own ``web_search_result`` blocks, so this only steers the search."""
    return (
        "Research the following using web search and cite only real sources you "
        "actually opened. Do not invent URLs. Focus on what is winning right now — "
        "engagement, hooks, and the mechanics behind it.\n\n"
        f"TOPIC: {query}"
    )


def _query_strings(query: ResearchQuery) -> list[str]:
    """1-2 concrete search strings from the ask (deterministic, nothing invented —
    just the query's own niche/seed-terms/competitor)."""
    niche = (query.niche or "").strip()
    terms = [t.strip() for t in query.seed_terms if t and t.strip()]
    out: list[str] = []
    if query.intent == "competitor_creatives":
        base = (query.competitor or (terms[0] if terms else niche)).strip()
        if base:
            out.append(f"{base} best performing social posts hooks")
    elif query.intent == "find_communities":
        if niche:
            out.append(f"{niche} community where customers gather")
    else:
        if niche:
            out.append(niche)
    if terms and niche:
        out.append(f"{niche} {terms[0]}")
    seen: set[str] = set()
    cleaned: list[str] = []
    for q in out:
        q = q.strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            cleaned.append(q)
        if len(cleaned) >= 2:
            break
    return cleaned or ([niche] if niche else [])


def _extract_web_search_results(raw_json: str, *, limit: int) -> list[SearchResult]:
    """Parse a ``/v1/messages`` response and pull the REAL web-search hits.

    Walks ``content`` for ``web_search_tool_result`` blocks whose ``content`` is a
    LIST of ``web_search_result`` items (url/title[/page_age]); an error block
    (``content`` is a dict with ``error_code``) yields nothing for that block.
    url/title are verbatim; ``snippet`` prefers ``page_age``, else the title —
    never synthesized. A ``stop_reason == 'refusal'`` (the whole fallback chain
    declined) yields ``[]``. Defensive: never raises (returns ``[]``)."""
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    # A refusal on the FINAL response means the whole chain declined — honest empty.
    if data.get("stop_reason") == "refusal":
        return []
    content = data.get("content")
    if not isinstance(content, list):
        return []

    out: list[SearchResult] = []
    seen: set[str] = set()
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "web_search_tool_result":
            continue
        results = block.get("content")
        if not isinstance(results, list):  # an error block is a dict, not a list
            continue
        for item in results:
            if not isinstance(item, dict) or item.get("type") != "web_search_result":
                continue
            url = item.get("url")
            if not (isinstance(url, str) and url.strip()) or url.strip() in seen:
                continue
            title = item.get("title")
            title = title.strip() if isinstance(title, str) and title.strip() else None
            page_age = item.get("page_age")
            snippet = page_age.strip() if isinstance(page_age, str) and page_age.strip() else title
            seen.add(url.strip())
            out.append(SearchResult(url=url.strip(), title=title, snippet=snippet))
            if len(out) >= limit:
                return out
    return out
