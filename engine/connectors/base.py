"""Shared security primitives for the FB + Gmail connector scaffolds (bead xeu).

Builds to sec/reviews/conn-scaffold-security-requirements.md. Every connector is
**mock-default / disabled** (``enabled=False`` → :class:`ConnectorDisabledError`):
no live API call until ``enabled=True`` AND operator go-live AND sec re-vet of the
live wiring (same gate as de6). Sends/DMs additionally HARD-ESCALATE to a human
(439) — see :class:`ConnectorHeldError`.

Primitives here (all pure, testable, no secret ever logged):
  * ``redact`` — never let a token/secret reach a log/repr/error.
  * ``appsecret_proof`` — HMAC-SHA256(app_secret, access_token) on every Graph call.
  * ``verify_x_hub_signature_256`` — Meta webhook ingress is untrusted; reject forged.
  * ``pkce_pair`` / ``build_authorize_url`` — OAuth code flow with PKCE + state +
    exact-match redirect_uri allowlist; tokens NEVER in a URL/query string.
  * :class:`GatedConnector` — the enabled gate + ``_secure_request`` routed through
    the vetted boundary (assert_official_endpoint + resolve_and_pin, TLS pin-to-IP).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from urllib.parse import urlencode

from research.providers.firecrawl import HttpFetcher, HttpResponse, PinnedHttpsFetcher
from research.safety import assert_official_endpoint, resolve_and_pin


class ConnectorDisabledError(RuntimeError):
    """A live call was attempted while the connector is disabled (mock-default /
    not operator-green-lit). The safe default; never a silent live call."""


class ConnectorHeldError(RuntimeError):
    """A send/DM was attempted but is HELD — it must hard-escalate to a human
    (bead 439). No autonomous send until per-channel 439-lift + operator go-live."""


# ── secret hygiene (sec req B) ───────────────────────────────────────────────


def redact(value: str | None, *, keep: int = 4) -> str:
    """A log-safe view of a secret: only a length-tagged prefix, never the value.
    Use this anywhere a token/secret might otherwise reach a log/repr/error."""
    if not value:
        return "<none>"
    return f"<redacted:{len(value)}ch:{value[:keep]}…>" if len(value) > keep else "<redacted>"


# ── Meta appsecret_proof (sec req C) ─────────────────────────────────────────


def appsecret_proof(app_secret: str, access_token: str) -> str:
    """HMAC-SHA256(app_secret, access_token) hex — required on EVERY Graph call so
    a stolen access token alone is unusable. Computed server-side only."""
    return hmac.new(app_secret.encode(), access_token.encode(), hashlib.sha256).hexdigest()


def verify_x_hub_signature_256(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    """Verify a Meta webhook 'X-Hub-Signature-256: sha256=<hex>' over the RAW body.
    Constant-time compare; reject missing/forged. Webhook ingress is untrusted."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


# ── OAuth code flow with PKCE + state + redirect allowlist (sec req D) ────────


def pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def build_authorize_url(
    *,
    authorize_endpoint: str,
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: str,
    code_challenge: str,
    allowed_redirect_uris: frozenset[str],
) -> str:
    """Build the OAuth authorize URL — redirect_uri must EXACT-MATCH the allowlist
    (no open redirect), ``state`` (CSRF) + PKCE ``code_challenge`` required. No
    token/secret is ever placed in the URL (only client_id + the public challenge)."""
    if redirect_uri not in allowed_redirect_uris:
        raise ValueError(f"redirect_uri {redirect_uri!r} not in the allowlist")
    if not state:
        raise ValueError("state (CSRF) is required")
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{authorize_endpoint}?{urlencode(params)}"


# ── gated connector base ─────────────────────────────────────────────────────


class GatedConnector:
    """Mixin: the disabled gate + a secure request routed through the de6 boundary.

    Concrete connectors set ``provider_name`` (the OFFICIAL_API_HOSTS key) and call
    ``_secure_request`` only after passing ``_require_enabled``. Tokens go in the
    Authorization header / body — NEVER a URL — and are never logged.
    """

    provider_name: str = ""

    def __init__(self, *, enabled: bool = False, fetcher: HttpFetcher | None = None,
                 timeout: float = 15.0, resolver=None) -> None:
        self._enabled = enabled            # mock-default: no live call unless True
        self._fetcher = fetcher or PinnedHttpsFetcher()
        self._timeout = timeout
        self._resolver = resolver

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise ConnectorDisabledError(
                f"{self.provider_name} connector is disabled (mock-default). Enable "
                "only after sec re-vet + operator go-live (bead xeu / de6 gate)."
            )

    def _secure_request(self, *, api_base: str, host: str, method: str, path: str,
                        headers: dict[str, str], body: bytes | None = None) -> HttpResponse:
        """Official-host-only + pin-to-IP request (the de6 secure path). Caller has
        already passed ``_require_enabled``. ``headers`` carries Authorization."""
        assert_official_endpoint(api_base, self.provider_name)
        pinned_ip = resolve_and_pin(host, resolver=self._resolver)
        return self._fetcher.request(
            method=method, ip=pinned_ip, host=host, path=path,
            headers=headers, body=body, timeout=self._timeout,
        )
