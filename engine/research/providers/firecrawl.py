"""FirecrawlProvider — official-API web fetch with a SECURE, GATED live client.

Live fetch (bead de6 / a9m.2 follow-up) behind the 1mk.4 safety boundary
(#50/#64). It is **disabled by default** (mock-default): no live fetch happens
unless ``enabled=True`` is explicitly passed AND the operator green-lights go-live;
sec re-vets the live impl first (the 1mk.4 go-live conditions).

Security model (every live request):
  1. ``assert_safe_url(target)`` — SSRF guard on the URL we ask Firecrawl to scrape
     (https-only, no private/loopback/metadata/obfuscated host, no creds).
  2. ``assert_official_endpoint(api_base, "firecrawl")`` — we only ever connect to
     the official ``api.firecrawl.dev`` over TLS (official-API-only).
  3. ``resolve_and_pin(api_host)`` — getaddrinfo → re-validate EVERY resolved IP →
     return ONE vetted IP; we connect to THAT IP with TLS SNI/Host = the hostname
     (the unskippable F2 recheck + pin-to-IP-before-connect — defeats rebinding).
  4. rate-limited; key-from-pack (constructor), never a vendored ``.env``.

The byte-level I/O is behind the :class:`HttpFetcher` seam so tests assert the
pinning/gating/SSRF wiring with a fake (no real network); :class:`PinnedHttpsFetcher`
is the real stdlib client used at go-live.
"""

from __future__ import annotations

import json
import socket
import ssl
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from research.adapter import Channel, Document, ProviderResult, ResearchQuery
from research.safety import (
    RateLimiter,
    assert_official_endpoint,
    assert_safe_url,
    resolve_and_pin,
)

_FIRECRAWL_CHANNELS = frozenset(
    {Channel.R_TATTOOS, Channel.INSTAGRAM_HASHTAG, Channel.PINTEREST, Channel.TIKTOK, Channel.WEB}
)
FIRECRAWL_API_BASE = "https://api.firecrawl.dev"
_API_HOST = "api.firecrawl.dev"
_SCRAPE_PATH = "/v1/scrape"
_SEARCH_PATH = "/v1/search"


class FirecrawlDisabledError(RuntimeError):
    """A live fetch was attempted while the provider is disabled (mock-default /
    not operator-green-lit). The safe default; never a silent live call."""


@dataclass(frozen=True)
class SearchResult:
    """One real Firecrawl ``/v1/search`` hit — a citable research source.

    Every field is copied verbatim from the official API response; the research
    agent NEVER fabricates a url/title/snippet (the slice-3 honesty gate). A hit
    with no real ``url`` is dropped upstream — it is not a citable source.
    """

    url: str
    title: str | None = None
    snippet: str | None = None


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: str
    tls_verified: bool = True


@runtime_checkable
class HttpFetcher(Protocol):
    """The byte-level seam. Implementations MUST connect to ``ip`` (already vetted
    by resolve_and_pin) while presenting ``host`` for TLS SNI + the Host header."""

    def request(self, *, method: str, ip: str, host: str, path: str,
                headers: dict[str, str], body: bytes | None, timeout: float) -> HttpResponse: ...


class PinnedHttpsFetcher:
    """Real stdlib HTTPS client that connects to the PINNED IP with a verified TLS
    context and SNI=host (used at go-live; exercised by sec/operator, not CI)."""

    def request(self, *, method, ip, host, path, headers, body, timeout):  # pragma: no cover
        ctx = ssl.create_default_context()  # verification ON (never CERT_NONE)
        raw = socket.create_connection((ip, 443), timeout=timeout)
        try:
            with ctx.wrap_socket(raw, server_hostname=host) as tls:  # SNI = hostname
                lines = [f"{method} {path} HTTP/1.1", f"Host: {host}", "Connection: close"]
                lines += [f"{k}: {v}" for k, v in headers.items()]
                if body is not None:
                    lines.append(f"Content-Length: {len(body)}")
                data = ("\r\n".join(lines) + "\r\n\r\n").encode() + (body or b"")
                tls.sendall(data)
                chunks = []
                while True:
                    b = tls.recv(65536)
                    if not b:
                        break
                    chunks.append(b)
        finally:
            raw.close()
        head, _, payload = b"".join(chunks).partition(b"\r\n\r\n")
        status = int(head.split(b" ", 2)[1]) if b" " in head else 0
        return HttpResponse(status=status, body=payload.decode("utf-8", "replace"))


class FirecrawlProvider:
    """Official-API web provider. Live fetch is GATED (disabled by default)."""

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
        self._api_key = api_key            # key-from-pack; never a vendored .env
        self._api_base = FIRECRAWL_API_BASE
        self._enabled = enabled            # mock-default: no live fetch unless True
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
        """Run a real web search via the official Firecrawl ``/v1/search`` API.

        Same SECURE + GATED path as :meth:`fetch` — gated mock-default,
        official-API-only, pin-to-IP-before-connect (F2), rate-limited,
        key-from-pack — but POSTs a *query* to ``/v1/search`` and returns the real
        hits (url/title/snippet) straight off the response.

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
        # 2. official-API-only + pin-to-IP (F2): we connect to api.firecrawl.dev.
        assert_official_endpoint(self._api_base, "firecrawl")
        pinned_ip = resolve_and_pin(_API_HOST, resolver=self._resolver)
        # 3. rate-limit + the request.
        self._limiter.acquire(self._clock())
        body = json.dumps({"query": query.strip(), "limit": int(limit)}).encode("utf-8")
        resp = self._fetcher.request(
            method="POST", ip=pinned_ip, host=_API_HOST, path=_SEARCH_PATH,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            body=body, timeout=self._timeout,
        )
        if resp.status >= 400:
            raise RuntimeError(f"Firecrawl search returned HTTP {resp.status}")
        return _extract_search_results(resp.body, limit=int(limit))

    def gather(self, query: ResearchQuery) -> ProviderResult:
        # The query -> Signal/Community normalization over Firecrawl search results
        # is the next step (de6 follow-up); the secure fetch primitive above is the
        # safety-critical core. Until then the router degrades to the fixture.
        raise NotImplementedError(
            "FirecrawlProvider.gather: query->Signal normalization pending (uses the "
            "now-live fetch + Firecrawl search); router uses the fixture until then."
        )


def _extract_search_results(raw_json: str, *, limit: int) -> list[SearchResult]:
    """Best-effort parse of a Firecrawl ``/v1/search`` response into typed hits.

    Defensive: never raises on an unexpected shape (returns ``[]`` instead). Every
    returned field is copied verbatim from the response ``data[]`` items
    (``url`` / ``title`` / ``description``|``snippet``) — nothing is synthesized.
    A hit with no usable ``url`` is dropped (not a citable source).
    """
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return []
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
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
