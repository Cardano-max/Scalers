"""FirecrawlProvider — official-API web research with a SECURE, GATED live client.

Live fetch/search (bead de6 / a9m.2 / p3.0-B) behind the 1mk.4 safety boundary
(#50/#64). It is **disabled by default** (mock-default): no live call happens
unless ``enabled=True`` is explicitly passed AND the operator green-lights go-live;
sec re-vets the live impl first (the 1mk.4 go-live conditions).

Security model (every live request), via :func:`research.providers._http.secure_post_json`:
  1. ``assert_safe_url(target)`` — SSRF guard on a URL we ask Firecrawl to *scrape*
     (https-only, no private/loopback/metadata/obfuscated host, no creds). (fetch only)
  2. ``assert_official_endpoint(api_base, "firecrawl")`` — we only ever connect to
     the official ``api.firecrawl.dev`` over TLS (official-API-only).
  3. ``resolve_and_pin(api_host)`` — getaddrinfo → re-validate EVERY resolved IP →
     return ONE vetted IP; we connect to THAT IP with TLS SNI/Host = the hostname
     (the unskippable F2 recheck + pin-to-IP-before-connect — defeats rebinding).
  4. rate-limited; key-from-pack/env (constructor), never a vendored ``.env``.

p3.0-B fills :meth:`gather` — the ``ResearchQuery`` -> real ``/v2/search`` ->
``Signal``/``Community``/``Creative`` normalization the ResearchRouter fans out and
merges. Every returned source URL comes from a real Firecrawl response; an empty
or erroring response yields an empty, honestly-noted result — never a fabrication.
"""

from __future__ import annotations

import json
from typing import Any

from research.adapter import (
    Channel,
    Community,
    Creative,
    Document,
    ProviderResult,
    ResearchQuery,
    Signal,
)
from research.providers._http import (  # shared vetted egress seam (re-exported)
    HttpFetcher,
    HttpResponse,
    PinnedHttpsFetcher,
    SearchResult,
    secure_post_json,
)
from research.safety import (
    RateLimiter,
    assert_official_endpoint,
    assert_safe_url,
    resolve_and_pin,
)

__all__ = [
    "FirecrawlProvider",
    "FirecrawlDisabledError",
    "SearchResult",
    "HttpResponse",
    "HttpFetcher",
    "PinnedHttpsFetcher",
]

_FIRECRAWL_CHANNELS = frozenset(
    {Channel.R_TATTOOS, Channel.INSTAGRAM_HASHTAG, Channel.PINTEREST, Channel.TIKTOK, Channel.WEB}
)
FIRECRAWL_API_BASE = "https://api.firecrawl.dev"
_API_HOST = "api.firecrawl.dev"
_SCRAPE_PATH = "/v1/scrape"
_SEARCH_PATH = "/v2/search"  # official Firecrawl v2 search endpoint

# A web search hit is a WEB-channel source regardless of the query's tattoo-native
# channels: the evidence literally came from a web search, so we label it honestly.
_WEB = Channel.WEB


class FirecrawlDisabledError(RuntimeError):
    """A live call was attempted while the provider is disabled (mock-default /
    not operator-green-lit). The safe default; never a silent live call."""


class FirecrawlProvider:
    """Official-API web provider. Live fetch/search is GATED (disabled by default)."""

    name = "firecrawl"
    channels: frozenset[Channel] = _FIRECRAWL_CHANNELS

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
    ) -> None:
        self._api_key = api_key            # key-from-pack/env; never a vendored .env
        self._api_base = FIRECRAWL_API_BASE
        self._enabled = enabled            # mock-default: no live call unless True
        self._fetcher = fetcher or PinnedHttpsFetcher()
        self._limiter = RateLimiter(rate=rate, burst=burst)
        self._timeout = timeout
        self._resolver = resolver          # injectable getaddrinfo (tests); None=real
        import time

        self._clock = clock or time.monotonic

    @property
    def enabled(self) -> bool:
        return self._enabled

    def fetch(self, url: str) -> Document:
        """Scrape ``url`` via the official Firecrawl API — secure + gated."""
        # 1. SSRF guard on the target we ask Firecrawl to scrape.
        assert_safe_url(url)
        # 2. Gate: never a live call unless explicitly enabled + operator-green-lit.
        if not self._enabled:
            raise FirecrawlDisabledError(
                "Firecrawl live fetch is disabled (mock-default). Enable only after "
                "sec re-vet + operator go-live (bead de6 / 1mk.4 conditions)."
            )
        if not self._api_key:
            raise FirecrawlDisabledError("no Firecrawl API key (key-from-pack required)")
        # 3. official-API-only + pin-to-IP (F2): we connect to api.firecrawl.dev.
        assert_official_endpoint(self._api_base, "firecrawl")
        pinned_ip = resolve_and_pin(_API_HOST, resolver=self._resolver)
        # 4. rate-limit + the request.
        self._limiter.acquire(self._clock())
        body = json.dumps({"url": url, "formats": ["markdown"]}).encode("utf-8")
        resp = self._fetcher.request(
            method="POST", ip=pinned_ip, host=_API_HOST, path=_SCRAPE_PATH,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            body=body, timeout=self._timeout,
        )
        if resp.status >= 400:
            raise RuntimeError(f"Firecrawl returned HTTP {resp.status}")
        return Document(
            url=url, text=_extract_markdown(resp.body), title=None,
            fetched_via="firecrawl", tls_verified=resp.tls_verified,
        )

    def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        """Run a real web search via the official Firecrawl ``/v2/search`` API.

        Same SECURE + GATED path as :meth:`fetch` (gated mock-default,
        official-API-only, pin-to-IP-before-connect (F2), rate-limited,
        key-from-pack/env) routed through the shared :func:`secure_post_json`
        boundary — but POSTs a *query* to ``/v2/search`` and returns the real hits
        (url/title/snippet) straight off the response.

        HONESTY GATE: returns ONLY what the API returned. An empty / malformed /
        odd-shaped response yields an empty list — never an invented citation.
        """
        # 1. Gate: never a live call unless explicitly enabled + operator-green-lit.
        if not self._enabled:
            raise FirecrawlDisabledError(
                "Firecrawl live search is disabled (mock-default). Enable only after "
                "sec re-vet + operator go-live (bead de6 / 1mk.4 conditions)."
            )
        if not self._api_key:
            raise FirecrawlDisabledError("no Firecrawl API key (key-from-pack required)")
        if not (query and query.strip()):
            raise ValueError("empty search query")
        # 2-4. official-API-only + pin-to-IP (F2) + rate-limit + request — all in the
        # shared vetted egress seam (no boundary logic duplicated per provider).
        resp = secure_post_json(
            fetcher=self._fetcher,
            provider_name="firecrawl",
            api_base=self._api_base,
            api_host=_API_HOST,
            path=_SEARCH_PATH,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            payload={"query": query.strip(), "limit": int(limit)},
            limiter=self._limiter,
            clock=self._clock,
            timeout=self._timeout,
            resolver=self._resolver,
        )
        return _extract_search_results(resp.body, limit=int(limit))

    def gather(self, query: ResearchQuery) -> ProviderResult:
        """Run REAL web research for ``query`` and normalize it into the router's
        typed shapes (the p3.0-B fill — replaces the old NotImplementedError stub).

        Flow: build concrete search query strings from the ask -> ``/v2/search``
        per string (the secure, gated path above) -> map each real hit to a
        ``Signal`` (map_market), ``Community`` (find_communities) or ``Creative``
        (competitor_creatives), and carry the raw {query,url,title,snippet} hit in
        ``ProviderResult.sources`` for the router to dedupe + persist as a citation.

        HONESTY GATE: every url/title/snippet is verbatim from a real Firecrawl
        response. If the provider is disabled or has no key, this raises (the router
        notes it and degrades to the fixture/empty) — it never invents a source.
        An empty search yields an empty, honestly-noted result.
        """
        # Gated/keyed checks surface as an exception the router catches -> honest
        # degrade note (we do NOT silently return a fixture from here).
        if not self._enabled:
            raise FirecrawlDisabledError(
                "Firecrawl live research is disabled (mock-default); router degrades."
            )
        if not self._api_key:
            raise FirecrawlDisabledError("no Firecrawl API key (key-from-pack/env required)")

        search_terms = _query_strings(query)
        per_query = max(1, query.limit // max(1, len(search_terms)))
        raw_sources: list[dict[str, Any]] = []
        notes: list[str] = []
        seen: set[str] = set()
        for term in search_terms:
            try:
                hits = self.search(term, limit=per_query)
            except Exception as exc:  # noqa: BLE001 — record real reason, keep going
                notes.append(f"firecrawl search failed for {term!r}: {type(exc).__name__}: {exc}")
                continue
            for h in hits:
                if h.url in seen:
                    continue
                seen.add(h.url)
                raw_sources.append(
                    {"query": term, "url": h.url, "title": h.title, "snippet": h.snippet}
                )

        if not raw_sources:
            notes.append("firecrawl: web search returned no usable sources (honest-empty)")
            return ProviderResult(notes=tuple(notes))

        signals: list = []
        communities: list = []
        creatives: list = []
        for rank, s in enumerate(raw_sources):
            conf = _rank_confidence(rank)
            text = s["snippet"] or s["title"] or s["url"]
            if query.intent == "find_communities":
                communities.append(
                    Community(
                        name=s["title"] or s["url"], channel=_WEB,
                        entry_tactic="engage authentically on this page/source; "
                        "link only when it adds value",
                        url=s["url"], size_hint=None,
                    )
                )
            elif query.intent == "competitor_creatives":
                creatives.append(
                    Creative(
                        competitor=query.competitor or query.niche, channel=_WEB,
                        angle=s["title"] or text, hook=None, format=None,
                        url=s["url"], confidence=conf,
                        evidence=(s["query"],),
                    )
                )
            else:  # map_market (default) — demand/pain/angle evidence
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
            sources=tuple(raw_sources),  # raw, verbatim, citable hits for persistence
            notes=tuple(notes),
        )


# ── query construction + ranking (deterministic, no fabrication) ──────────────


def _query_strings(query: ResearchQuery) -> list[str]:
    """Build 1-3 concrete web-search strings from a ResearchQuery. Deterministic;
    no LLM (the router gather path is provider I/O, not an agent). The strings are
    just the ask's own niche/seed-terms/competitor — nothing invented."""
    niche = (query.niche or "").strip()
    terms = [t.strip() for t in query.seed_terms if t and t.strip()]
    out: list[str] = []
    if query.intent == "competitor_creatives":
        comp = (query.competitor or (terms[0] if terms else "")).strip()
        base = comp or niche
        if base:
            out.append(f"{base} tattoo ads campaign")
            out.append(f"{base} marketing instagram")
    elif query.intent == "find_communities":
        if niche:
            out.append(f"{niche} community forum")
        if terms:
            out.append(f"{terms[0]} tattoo community")
    else:  # map_market
        if niche:
            out.append(niche)
        if terms:
            out.append(f"{niche} {terms[0]}".strip())
        out.append(f"{niche} trends".strip() if niche else "")
    # de-dup, drop empties, cap at 3
    seen: set[str] = set()
    cleaned: list[str] = []
    for q in out:
        q = q.strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            cleaned.append(q)
        if len(cleaned) >= 3:
            break
    return cleaned or ([niche] if niche else [])


def _rank_confidence(rank: int) -> float:
    """Descending confidence by search rank (top hit highest), floored at 0.4 so
    the router keeps every real hit but orders them by where the search ranked them."""
    return max(0.4, round(0.85 - 0.05 * rank, 2))


def _extract_search_results(raw_json: str, *, limit: int) -> list[SearchResult]:
    """Best-effort parse of a Firecrawl search response into typed hits.

    Handles BOTH shapes: v2 ``data`` is a dict of result lists (``web``/``news``);
    v1 ``data`` is a flat list. Defensive: never raises on an unexpected shape
    (returns ``[]`` instead). Every returned field is copied verbatim from the
    response items (``url`` / ``title`` / ``description``|``snippet``) — nothing is
    synthesized. A hit with no usable ``url`` is dropped (not a citable source).
    """
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return []
    container = data.get("data") if isinstance(data, dict) else None
    items: list = []
    if isinstance(container, list):              # v1: flat list of hits
        items = container
    elif isinstance(container, dict):            # v2: {web:[...], news:[...], ...}
        for key in ("web", "news"):
            sub = container.get(key)
            if isinstance(sub, list):
                items.extend(sub)
    out: list[SearchResult] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not (isinstance(url, str) and url.strip()):
            continue
        title = item.get("title")
        snippet = item.get("description")
        if not (isinstance(snippet, str) and snippet.strip()):
            snippet = item.get("snippet")
        out.append(
            SearchResult(
                url=url.strip(),
                title=title.strip() if isinstance(title, str) and title.strip() else None,
                snippet=snippet.strip() if isinstance(snippet, str) and snippet.strip() else None,
            )
        )
        if len(out) >= limit:
            break
    return out


def _extract_markdown(raw_json: str) -> str:
    """Best-effort pull of the scraped text from a Firecrawl JSON response.
    Defensive: never raises on an unexpected shape (returns '' instead)."""
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return ""
    if isinstance(data, dict):
        inner = data.get("data", data)
        if isinstance(inner, dict):
            return str(inner.get("markdown") or inner.get("content") or "")
    return ""
