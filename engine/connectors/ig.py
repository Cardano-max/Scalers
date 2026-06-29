"""Instagram content-publish connector (Scalers team-lead item #3) — gated, secret-safe.

The publish half of the live "generate → approve → real IG post" path. Instagram
content publishing is served by the **Meta Graph API on ``graph.facebook.com``**
(the IG Graph API rides the same host as :class:`connectors.fb.FacebookConnector`),
so this connector reuses the ``facebook`` provider allowlist + the de6 secure
boundary (``assert_official_endpoint`` + ``resolve_and_pin`` + pin-to-IP) exactly
like ``fb.py``. ``appsecret_proof`` on EVERY Graph call (sec req C); the page token
lives in the request body / Authorization header, **never a URL**, and is **never
logged** (``__repr__`` redacts; errors carry the REAL Graph error, never a token).

The two-step IG Content Publishing flow (``post``):

  1. **create container** — ``POST /v25.0/{ig_business_account_id}/media`` with
     ``image_url`` + ``caption`` (+ ``access_token`` + ``appsecret_proof``) → returns
     a ``creation_id``,
  2. **publish** — ``POST /v25.0/{ig_business_account_id}/media_publish`` with that
     ``creation_id`` → returns the published ``media_id``,
  3. **permalink** — best-effort ``GET /v25.0/{media_id}?fields=permalink`` (token in
     the ``Authorization: Bearer`` header, never the URL) to resolve the public
     ``https://www.instagram.com/p/…`` link.

Gates / hygiene (same contract as :class:`connectors.fb.FacebookConnector`):

* **disabled by default** (``enabled=False`` → :class:`ConnectorDisabledError`):
  no live call until a caller explicitly enables it (sec re-vet + operator go-live),
* credentials are read **key-from-env** (``IG_BUSINESS_ACCOUNT_ID`` /
  ``LADIES8391_FB_PAGE_TOKEN`` / ``META_APP_SECRET``) — never hardcoded,
* a non-2xx from Graph raises a typed error carrying the REAL provider status +
  message; it is **NEVER** swallowed into a fake success.

NOTE (real-IG-post blockers): IG content publishing requires the
``instagram_content_publish`` permission AND — for any non-test user — **Meta App
Review** of that permission. ``image_url`` must be a publicly reachable JPEG. A real
post today is therefore blocked on (a) a valid, re-minted Meta token and (b) likely
Meta App Review; this connector does not paper over either — it fails carrying the
real Graph error.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from connectors.base import GatedConnector, appsecret_proof, redact

GRAPH_API_BASE = "https://graph.facebook.com"
_GRAPH_HOST = "graph.facebook.com"
_GRAPH_VERSION = "v25.0"

# Env keys the connector reads (key-from-env; never a vendored .env, never logged).
# The PAGE token (LADIES8391_FB_PAGE_TOKEN) carries instagram_content_publish +
# instagram_manage_comments; LADIES8391_META_ACCESS_TOKEN is a fallback user token.
ENV_IG_BUSINESS_ACCOUNT_ID = "IG_BUSINESS_ACCOUNT_ID"
ENV_PAGE_TOKEN = "LADIES8391_FB_PAGE_TOKEN"
ENV_ACCESS_TOKEN_FALLBACK = "LADIES8391_META_ACCESS_TOKEN"
ENV_APP_SECRET = "META_APP_SECRET"


class InstagramConfigError(RuntimeError):
    """A live call was attempted without the required key-from-env creds
    (IG business account id / page token / app secret). Never echoes a token."""


class InstagramPublishError(RuntimeError):
    """An IG content-publishing call (``/media`` or ``/media_publish``) returned a
    non-2xx. Carries the REAL Graph status/message; never a token value. NEVER
    raised as a fake success."""


class InstagramReplyError(RuntimeError):
    """An IG comment-reply call (``/{comment_id}/replies``) returned a non-2xx.
    Carries the REAL Graph status/message; never a token value."""


@dataclass(frozen=True)
class InstagramPostResult:
    """The result of a real IG content publish — what the console deep-links to."""

    media_id: str
    creation_id: str
    permalink: str | None = None


@dataclass(frozen=True)
class InstagramReplyResult:
    """The result of a real IG comment reply."""

    reply_id: str
    comment_id: str


class InstagramConnector(GatedConnector):
    """Meta Graph (Instagram) content-publish connector — disabled by default.

    IG content publishing is hosted on ``graph.facebook.com``; ``provider_name`` is
    ``facebook`` so the existing official-host allowlist (sec ``OFFICIAL_API_HOSTS``)
    is honored without a new row. Build with explicit creds or :meth:`from_env`.
    """

    name = "instagram"
    provider_name = "facebook"  # IG Graph API rides graph.facebook.com (allowlisted)

    def __init__(
        self,
        *,
        ig_business_account_id: str | None = None,
        page_token: str | None = None,
        app_secret: str | None = None,
        **kw,
    ) -> None:
        # key-from-env (IG_BUSINESS_ACCOUNT_ID / LADIES8391_FB_PAGE_TOKEN /
        # META_APP_SECRET); never logged. The base mixin owns the enabled gate +
        # the secure (pin-to-IP) request path.
        super().__init__(**kw)
        self._ig_id = ig_business_account_id
        self._page_token = page_token
        self._app_secret = app_secret

    @classmethod
    def from_env(
        cls,
        *,
        enabled: bool = False,
        env: dict[str, str] | None = None,
        **kw,
    ) -> "InstagramConnector":
        """Build a connector reading creds from ``env`` (defaults to ``os.environ``)."""
        e = env if env is not None else os.environ
        return cls(
            ig_business_account_id=e.get(ENV_IG_BUSINESS_ACCOUNT_ID),
            page_token=e.get(ENV_PAGE_TOKEN) or e.get(ENV_ACCESS_TOKEN_FALLBACK),
            app_secret=e.get(ENV_APP_SECRET),
            enabled=enabled,
            **kw,
        )

    def __repr__(self) -> str:  # never leak the token in a repr/log
        return (
            f"InstagramConnector(enabled={self._enabled}, "
            f"ig_business_account_id={self._ig_id!r}, "
            f"token={redact(self._page_token)})"
        )

    # ── the two-step IG Content Publishing flow ───────────────────────────────

    def post(self, image_url: str, caption: str) -> InstagramPostResult:
        """Publish an IG feed image via the two-step Graph flow (create container →
        publish), then resolve the permalink. Disabled-by-default; raises on the
        REAL Graph error (never a fake success)."""
        self._require_enabled()
        proof = self._proof()  # also validates creds are present

        # Step 1 — create the media container. Token + proof in the BODY, never the
        # URL (sec req B/C); the path carries only the IG business account id.
        creation = self._graph_post(
            f"/{_GRAPH_VERSION}/{self._ig_id}/media",
            {"image_url": image_url, "caption": caption},
            proof,
            error_cls=InstagramPublishError,
            what="create media container",
        )
        creation_id = str(creation.get("id", ""))
        if not creation_id:
            raise InstagramPublishError(
                "ig create media container returned no creation_id"
            )

        # Step 2 — publish the container.
        published = self._graph_post(
            f"/{_GRAPH_VERSION}/{self._ig_id}/media_publish",
            {"creation_id": creation_id},
            proof,
            error_cls=InstagramPublishError,
            what="publish media",
        )
        media_id = str(published.get("id", ""))
        if not media_id:
            raise InstagramPublishError("ig media_publish returned no media id")

        # Step 3 — resolve the public permalink (best-effort; a permalink-fetch
        # failure does not undo an already-published post).
        permalink = self._fetch_permalink(media_id, proof)
        return InstagramPostResult(
            media_id=media_id, creation_id=creation_id, permalink=permalink
        )

    def reply_to_comment(self, comment_id: str, message: str) -> InstagramReplyResult:
        """Reply to an IG comment (the engagement / auto-reply path):
        ``POST /v25.0/{comment_id}/replies``. Disabled-by-default; raises on the REAL
        Graph error."""
        self._require_enabled()
        proof = self._proof()
        data = self._graph_post(
            f"/{_GRAPH_VERSION}/{comment_id}/replies",
            {"message": message},
            proof,
            error_cls=InstagramReplyError,
            what="reply to comment",
        )
        return InstagramReplyResult(
            reply_id=str(data.get("id", "")), comment_id=comment_id
        )

    # ── internals ─────────────────────────────────────────────────────────────

    def _proof(self) -> str:
        """appsecret_proof = HMAC-SHA256(app_secret, page_token). Validates that the
        key-from-env creds are present first (so a missing cred is a clear config
        error, not a confusing HMAC over ``None``)."""
        if not (self._ig_id and self._page_token and self._app_secret):
            raise InstagramConfigError(
                "instagram connector missing key-from-env "
                "ig_business_account_id/page_token/app_secret"
            )
        return appsecret_proof(self._app_secret, self._page_token)

    def _graph_post(
        self,
        path: str,
        fields: dict[str, str],
        proof: str,
        *,
        error_cls: type[RuntimeError],
        what: str,
    ) -> dict:
        """One Graph POST through the de6 secure boundary. ``access_token`` +
        ``appsecret_proof`` go in the JSON BODY (never the URL); a non-2xx raises
        ``error_cls`` carrying the REAL Graph error."""
        body = json.dumps(
            {
                **fields,
                "access_token": self._page_token,  # body, not ?access_token=
                "appsecret_proof": proof,
            }
        ).encode("utf-8")
        resp = self._secure_request(
            api_base=GRAPH_API_BASE,
            host=_GRAPH_HOST,
            method="POST",
            path=path,
            headers={"Content-Type": "application/json"},
            body=body,
        )
        if resp.status >= 400:
            raise error_cls(
                f"ig {what} failed: HTTP {resp.status} "
                f"{self._error_detail(resp.body)}"
            )
        return _safe_json(resp.body)

    def _fetch_permalink(self, media_id: str, proof: str) -> str | None:
        """Best-effort ``GET /{media_id}?fields=permalink``. The access token rides
        the ``Authorization: Bearer`` header (NEVER the URL); ``appsecret_proof`` is a
        non-reversible HMAC and is safe as a query param. Returns ``None`` on any
        failure rather than fabricating a link."""
        try:
            resp = self._secure_request(
                api_base=GRAPH_API_BASE,
                host=_GRAPH_HOST,
                method="GET",
                path=f"/{_GRAPH_VERSION}/{media_id}?fields=permalink&appsecret_proof={proof}",
                headers={"Authorization": f"Bearer {self._page_token}"},
            )
        except Exception:  # noqa: BLE001 — permalink is best-effort, post already shipped
            return None
        if resp.status >= 400:
            return None
        data = _safe_json(resp.body)
        link = data.get("permalink") if isinstance(data, dict) else None
        return str(link) if link else None

    def _error_detail(self, raw: str) -> str:
        """Extract the REAL Graph error (message/type/code) for a connector error.
        Graph error bodies never echo the access token; we additionally scrub the
        token defensively so it can never reach a log even if that ever changed."""
        detail = _error_detail(raw)
        if self._page_token and self._page_token in detail:
            detail = detail.replace(self._page_token, redact(self._page_token))
        return detail


def _safe_json(raw: bytes | str) -> dict:
    try:
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        out = json.loads(text)
        return out if isinstance(out, dict) else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _error_detail(raw: bytes | str) -> str:
    """Pull a provider error message out of a Graph error body — never a token."""
    data = _safe_json(raw)
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        msg = err.get("message") or err.get("type") or ""
        code = err.get("code")
        return f"{msg} (code {code})" if code is not None else str(msg or err)
    if err:
        return str(err)
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    return text[:200]
