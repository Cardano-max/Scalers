"""Shared secure-egress seam for the LIVE research providers (p3.0-B).

Both :class:`~research.providers.firecrawl.FirecrawlProvider` and
:class:`~research.providers.exa.ExaProvider` make official-API-only, SSRF-guarded,
pinned-IP, rate-limited POSTs to learn real, citable web sources. This module is
the ONE vetted egress primitive they share so the safety boundary is written
once, not copy-pasted per provider:

  * :class:`HttpResponse` / :class:`HttpFetcher` — the byte-level seam (a fake in
    tests asserts the wiring with no real network; :class:`PinnedHttpsFetcher` is
    the real stdlib client used at go-live).
  * :class:`SearchResult` — one real web-search hit (url/title/snippet), copied
    VERBATIM from the official API response — never fabricated.
  * :func:`secure_post_json` — composes the full boundary in order:
    ``assert_official_endpoint`` (official-API-only) -> ``resolve_and_pin`` (the
    unskippable F2 recheck: re-validate EVERY resolved IP, return ONE vetted IP to
    pin the connection to, defeating DNS rebinding) -> rate-limit -> request to the
    PINNED IP with TLS SNI/Host = the official hostname -> raise on HTTP >= 400.

No provider does raw network outside this seam.
"""

from __future__ import annotations

import json
import socket
import ssl
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from research.safety import RateLimiter, assert_official_endpoint, resolve_and_pin


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: str
    tls_verified: bool = True


@dataclass(frozen=True)
class SearchResult:
    """One real web-search hit (Firecrawl ``/v2/search`` or Exa ``/search``) — a
    citable research source.

    Every field is copied verbatim from the official API response; a provider
    NEVER fabricates a url/title/snippet (the honesty gate). A hit with no real
    ``url`` is dropped upstream — it is not a citable source.
    """

    url: str
    title: str | None = None
    snippet: str | None = None


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


def secure_post_json(
    *,
    fetcher: HttpFetcher,
    provider_name: str,
    api_base: str,
    api_host: str,
    path: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    limiter: RateLimiter,
    clock,
    timeout: float,
    resolver=None,
) -> HttpResponse:
    """Do ONE secure POST through the vetted boundary and return the response.

    Composes, in order: (1) official-API-only check on ``api_base`` (the provider
    may only call its allowlisted host over https); (2) ``resolve_and_pin`` the
    host — the F2 recheck of every resolved IP, returning ONE vetted IP to pin the
    connection to (there is no path to a connect-IP that did not pass the recheck,
    so DNS rebinding cannot swap in a private address); (3) rate-limit; (4) the
    request to the pinned IP with Host/SNI = the official hostname. ``payload`` is
    JSON-encoded here. Raises :class:`RuntimeError` on HTTP >= 400.
    """
    assert_official_endpoint(api_base, provider_name)
    pinned_ip = resolve_and_pin(api_host, resolver=resolver)
    limiter.acquire(clock())
    body = json.dumps(payload).encode("utf-8")
    resp = fetcher.request(
        method="POST", ip=pinned_ip, host=api_host, path=path,
        headers=headers, body=body, timeout=timeout,
    )
    if resp.status >= 400:
        raise RuntimeError(f"{provider_name} {path} returned HTTP {resp.status}")
    return resp
