"""ExaProvider — official-API semantic web search/discovery (p3.0-B live fill).

Exa semantic search for web discovery (trends, winning patterns, pain signals),
the second live backend for the Phase-3 content brain alongside Firecrawl. Like
the other live providers it carries the sec go-live gate and goes through the SAME
vetted egress boundary (:func:`research.providers._http.secure_post_json`):
official API over TLS, official-API-only host allowlist, the F2 resolve-and-pin
SSRF recheck, rate-limited, key-from-env/pack — **disabled by default**
(mock-default); no live call unless ``enabled=True`` + a key.

p3.0-B fills :meth:`search` (official ``POST /search``, ``type="auto"`` so Exa
auto-routes to its neural/deep mode where that serves the query best) and
:meth:`gather` (``ResearchQuery`` -> real results -> ``Signal``s + raw citable
sources the ResearchRouter merges + persists).

HONESTY GATE: every url/title/snippet is verbatim from a real Exa response. No
key / disabled / empty -> the router degrades honestly (note + empty) — never an
invented citation. (NOTE: as of p3.0-B no EXA_API_KEY is provisioned, so this
path is code-complete + unit-proven via the fake fetcher; at runtime it degrades
honestly until a key lands.)
"""

from __future__ import annotations

from typing import Any

from research.adapter import Channel, ProviderResult, ResearchQuery, Signal
from research.providers._http import (
    HttpFetcher,
    PinnedHttpsFetcher,
    SearchResult,
    secure_post_json,
)
from research.safety import RateLimiter

EXA_API_BASE = "https://api.exa.ai"
_API_HOST = "api.exa.ai"
_SEARCH_PATH = "/search"
_EXA_CHANNELS = frozenset({Channel.WEB})
_WEB = Channel.WEB


class ExaDisabledError(RuntimeError):
    """A live Exa call was attempted while disabled (mock-default / no key). The
    safe default; never a silent live call."""


class ExaProvider:
    """Official-API semantic search. Live client is GATED (disabled by default)."""

    name = "exa"
    channels: frozenset[Channel] = _EXA_CHANNELS

    def __init__(
        self,
        api_key: str | None = None,
        *,
        enabled: bool = False,
        fetcher: HttpFetcher | None = None,
        rate: float = 2.0,
        burst: int = 5,
        timeout: float = 15.0,
        clock=None,
        resolver=None,
        credits_per_call: float = 1.0,
    ) -> None:
        self._api_key = api_key            # key-from-env/pack; never a vendored .env
        self._api_base = EXA_API_BASE
        self._enabled = enabled            # mock-default: no live call unless True
        self._fetcher = fetcher or PinnedHttpsFetcher()
        self._limiter = RateLimiter(rate=rate, burst=burst)
        self._timeout = timeout
        self._resolver = resolver
        self._credits = credits_per_call
        import time

        self._clock = clock or time.monotonic

    @property
    def enabled(self) -> bool:
        return self._enabled

    def cost_estimate(self, query: ResearchQuery) -> float:
        return self._credits

    def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        """Run a real semantic web search via the official Exa ``POST /search``.

        ``type="auto"`` lets Exa pick neural (deep) vs keyword per query;
        ``contents.highlights`` returns verbatim excerpts we use as snippets. Goes
        through the shared vetted boundary (official-API-only, F2 pin-to-IP,
        rate-limited, key-from-env). HONESTY GATE: returns ONLY what Exa returned;
        an empty/odd response yields an empty list — never an invented citation.
        """
        if not self._enabled:
            raise ExaDisabledError(
                "Exa live search is disabled (mock-default). Enable only after sec "
                "vet + operator go-live + a provisioned EXA_API_KEY."
            )
        if not self._api_key:
            raise ExaDisabledError("no Exa API key (key-from-env/pack required)")
        if not (query and query.strip()):
            raise ValueError("empty search query")
        resp = secure_post_json(
            fetcher=self._fetcher,
            provider_name="exa",
            api_base=self._api_base,
            api_host=_API_HOST,
            path=_SEARCH_PATH,
            headers={"x-api-key": self._api_key, "Content-Type": "application/json"},
            payload={
                "query": query.strip(),
                "numResults": int(limit),
                "type": "auto",                       # neural/deep where it serves best
                "contents": {"highlights": True},     # verbatim snippet excerpts
            },
            limiter=self._limiter,
            clock=self._clock,
            timeout=self._timeout,
            resolver=self._resolver,
        )
        return _extract_exa_results(resp.body, limit=int(limit))

    def gather(self, query: ResearchQuery) -> ProviderResult:
        """Run REAL semantic web research and normalize to the router's shapes
        (the p3.0-B fill — replaces the NotImplementedError stub).

        Exa serves the WEB channel: every hit becomes a WEB ``Signal`` plus a raw
        {query,url,title,snippet} citable source for the router to dedupe + persist.
        HONESTY GATE: disabled/no-key raises (router notes + degrades); an empty
        search yields an empty, honestly-noted result — never a fabricated source.
        """
        if not self._enabled:
            raise ExaDisabledError("Exa live research is disabled (mock-default); router degrades.")
        if not self._api_key:
            raise ExaDisabledError("no Exa API key (key-from-env/pack required)")

        terms = _query_strings(query)
        per_query = max(1, query.limit // max(1, len(terms)))
        raw_sources: list[dict[str, Any]] = []
        notes: list[str] = []
        seen: set[str] = set()
        for term in terms:
            try:
                hits = self.search(term, limit=per_query)
            except Exception as exc:  # noqa: BLE001 — record real reason, keep going
                notes.append(f"exa search failed for {term!r}: {type(exc).__name__}: {exc}")
                continue
            for h in hits:
                if h.url in seen:
                    continue
                seen.add(h.url)
                raw_sources.append(
                    {"query": term, "url": h.url, "title": h.title, "snippet": h.snippet}
                )

        if not raw_sources:
            notes.append("exa: semantic search returned no usable sources (honest-empty)")
            return ProviderResult(notes=tuple(notes))

        signals = tuple(
            Signal(
                text=(s["snippet"] or s["title"] or s["url"]),
                channel=_WEB, kind="demand",
                confidence=max(0.4, round(0.85 - 0.05 * rank, 2)),
                url=s["url"], evidence=(s["query"],),
            )
            for rank, s in enumerate(raw_sources)
        )
        return ProviderResult(signals=signals, sources=tuple(raw_sources), notes=tuple(notes))


def _query_strings(query: ResearchQuery) -> list[str]:
    """1-2 concrete semantic search strings from the ask (deterministic, no LLM,
    nothing invented — just the query's own niche/seed-terms/competitor)."""
    niche = (query.niche or "").strip()
    terms = [t.strip() for t in query.seed_terms if t and t.strip()]
    out: list[str] = []
    if query.intent == "competitor_creatives":
        base = (query.competitor or (terms[0] if terms else niche)).strip()
        if base:
            out.append(f"{base} tattoo marketing campaign")
    elif query.intent == "find_communities":
        if niche:
            out.append(f"{niche} community")
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


def _extract_exa_results(raw_json: str, *, limit: int) -> list[SearchResult]:
    """Parse an Exa ``/search`` response (``{"results": [{url,title,highlights,text,...}]}``)
    into typed hits. Defensive: never raises (returns ``[]``). url/title are
    verbatim; snippet prefers the first verbatim ``highlights`` excerpt, else a
    real ``text`` prefix — never synthesized. No ``url`` -> dropped."""
    import json

    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return []
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return []
    out: list[SearchResult] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not (isinstance(url, str) and url.strip()):
            continue
        title = item.get("title")
        snippet = _exa_snippet(item)
        out.append(
            SearchResult(
                url=url.strip(),
                title=title.strip() if isinstance(title, str) and title.strip() else None,
                snippet=snippet,
            )
        )
        if len(out) >= limit:
            break
    return out


def _exa_snippet(item: dict[str, Any]) -> str | None:
    """A verbatim snippet from an Exa result: first highlight, else a text prefix.
    Returns None rather than inventing one."""
    highlights = item.get("highlights")
    if isinstance(highlights, list):
        for h in highlights:
            if isinstance(h, str) and h.strip():
                return h.strip()
    text = item.get("text")
    if isinstance(text, str) and text.strip():
        t = text.strip()
        return t[:300] + ("…" if len(t) > 300 else "")
    snippet = item.get("snippet")
    if isinstance(snippet, str) and snippet.strip():
        return snippet.strip()
    return None
