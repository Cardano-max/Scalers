"""Real Gmail connector (demo live slice) — gated, stdlib-only, secret-safe.

The send half of the live "generate → approve → real send" path. Unlike the
Meta/Graph connector (which routes through the de6 firecrawl boundary), this
connector talks to Google with the **Python standard library only** (``urllib`` +
``email`` + ``base64``) — no new dependency — through a single injectable
``transport`` seam so tests assert the exact request without touching the network.

Flow (verified live 2026-06-29, scope ``https://www.googleapis.com/auth/gmail.send``):

  1. exchange the long-lived refresh token for a short-lived access token at
     ``https://oauth2.googleapis.com/token`` (``grant_type=refresh_token``),
  2. build an RFC822 message, base64url-encode it,
  3. ``POST {"raw": ...}`` to the Gmail ``users.messages.send`` endpoint with an
     ``Authorization: Bearer <access_token>`` header.

Gates / hygiene (same contract as :class:`connectors.fb.FacebookConnector`):

* **disabled by default** (``enabled=False`` → :class:`ConnectorDisabledError`):
  no live call until a caller explicitly enables it (only ``approve_and_publish``
  does, with real creds, on an operator-approved action),
* credentials are read **key-from-env** (``GMAIL_CLIENT_ID`` /
  ``GMAIL_CLIENT_SECRET`` / ``LADIES8391_GMAIL_OAUTH_REFRESH``) — never hardcoded,
* tokens live in the body / Authorization header, **never a URL**, and are
  **never logged** (``__repr__`` redacts; errors never echo a token).
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from connectors.base import GatedConnector, redact
from connectors.mail_message import AttachmentReceipt, build_mail_message

# Official Google endpoints (also on the gmail OFFICIAL_API_HOSTS allowlist).
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
SEND_ENDPOINT = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

# Env keys the connector reads (key-from-env; never a vendored .env, never logged).
ENV_CLIENT_ID = "GMAIL_CLIENT_ID"
ENV_CLIENT_SECRET = "GMAIL_CLIENT_SECRET"
ENV_REFRESH_TOKEN = "LADIES8391_GMAIL_OAUTH_REFRESH"


class GmailAuthError(RuntimeError):
    """The refresh→access token exchange failed (bad/expired refresh token, etc.).
    Carries the real provider status; never a token value."""


class GmailSendError(RuntimeError):
    """The Gmail ``users.messages.send`` call returned a non-2xx. Carries the real
    provider status/message; never a token value. NEVER raised as a fake success."""


@dataclass(frozen=True)
class HttpResult:
    """The minimal HTTP response the transport seam returns."""

    status: int
    body: bytes


@dataclass(frozen=True)
class GmailSendResult:
    """The result of a real Gmail send — what the console deep-links to.

    ``attachments`` records exactly what rode along (filename + sha256 + size,
    never the content bytes) so the send audit can state what was attached."""

    message_id: str
    deep_link: str | None
    thread_id: str | None = None
    to: str | None = None
    subject: str | None = None
    attachments: tuple[AttachmentReceipt, ...] = ()


# The injectable transport seam: a callable that performs one HTTPS request and
# returns an :class:`HttpResult`. The default uses urllib; tests inject a fake.
Transport = Callable[..., HttpResult]


def urllib_transport(*, method: str, url: str, headers: dict[str, str],
                     body: bytes | None, timeout: float) -> HttpResult:
    """Default stdlib transport: one HTTPS request via ``urllib`` (no new dep).

    A non-2xx is returned as an :class:`HttpResult` (not raised) so the connector
    can surface the REAL provider status/body in its own typed error — never a
    silent success.
    """
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https only)
            return HttpResult(status=resp.status, body=resp.read())
    except urllib.error.HTTPError as exc:  # 4xx/5xx still carry a JSON error body
        return HttpResult(status=exc.code, body=exc.read())


class GmailConnector(GatedConnector):
    """Real Gmail ``users.messages.send`` connector — disabled by default.

    Build with explicit creds, or :meth:`from_env` to read them key-from-env. The
    live send happens only after :meth:`_require_enabled` passes (the base gate).
    """

    name = "gmail"
    provider_name = "gmail"

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
        transport: Transport | None = None,
        enabled: bool = False,
        timeout: float = 15.0,
    ) -> None:
        # key-from-env; never logged. The base mixin owns the enabled gate.
        super().__init__(enabled=enabled, timeout=timeout)
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._transport: Transport = transport or urllib_transport

    @classmethod
    def from_env(
        cls,
        *,
        enabled: bool = False,
        env: dict[str, str] | None = None,
        transport: Transport | None = None,
        timeout: float = 15.0,
    ) -> "GmailConnector":
        """Build a connector reading creds from ``env`` (defaults to ``os.environ``)."""
        import os

        e = env if env is not None else os.environ
        return cls(
            client_id=e.get(ENV_CLIENT_ID),
            client_secret=e.get(ENV_CLIENT_SECRET),
            refresh_token=e.get(ENV_REFRESH_TOKEN),
            transport=transport,
            enabled=enabled,
            timeout=timeout,
        )

    def __repr__(self) -> str:  # never leak a secret in a repr/log
        return (
            f"GmailConnector(enabled={self._enabled}, "
            f"client_id={redact(self._client_id)}, "
            f"refresh_token={redact(self._refresh_token)})"
        )

    # ── the real send path ────────────────────────────────────────────────────

    def send(
        self,
        to: str,
        subject: str,
        body: str,
        *,
        from_addr: str | None = None,
        attachments: Sequence[Mapping[str, Any]] | None = None,
    ) -> GmailSendResult:
        """Send a real email via Gmail. Disabled-by-default; raises on the real
        provider error (never a fake success).

        ``attachments`` (optional): list of ``{filename, content_bytes, mime_type}``.
        Validated FAIL-CLOSED before any network call (allowed types png/jpeg/webp/pdf,
        20MB total cap — :mod:`connectors.mail_message`): an invalid attachment raises
        :class:`connectors.mail_message.MailAttachmentError` and NOTHING is sent — a
        promised attachment is never silently dropped."""
        self._require_enabled()
        if not (self._client_id and self._client_secret and self._refresh_token):
            raise GmailAuthError(
                "gmail connector missing client_id/client_secret/refresh_token "
                "(key-from-env required)"
            )
        # Build (and thereby validate) the message BEFORE the token exchange: an
        # invalid attachment must refuse the send without touching the network.
        raw, receipts = self._build_raw_message(
            to=to, subject=subject, body=body, from_addr=from_addr, attachments=attachments
        )
        access_token = self._exchange_refresh_token()
        resp = self._transport(
            method="POST",
            url=SEND_ENDPOINT,
            headers={
                "Authorization": f"Bearer {access_token}",  # header, never a URL
                "Content-Type": "application/json",
            },
            body=json.dumps({"raw": raw}).encode("utf-8"),
            timeout=self._timeout,
        )
        if resp.status >= 400:
            raise GmailSendError(
                f"gmail send failed: HTTP {resp.status} {_error_detail(resp.body)}"
            )
        data = _safe_json(resp.body)
        message_id = str(data.get("id", "")) if isinstance(data, dict) else ""
        thread_id = str(data.get("threadId", "")) if isinstance(data, dict) else ""
        return GmailSendResult(
            message_id=message_id,
            thread_id=thread_id or None,
            deep_link=f"https://mail.google.com/mail/u/0/#sent/{message_id}" if message_id else None,
            to=to,
            subject=subject,
            attachments=receipts,
        )

    def _exchange_refresh_token(self) -> str:
        """Exchange the refresh token for a short-lived access token (token in the
        BODY, never a URL). Raises :class:`GmailAuthError` on the real failure."""
        form = urllib.parse.urlencode(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")
        resp = self._transport(
            method="POST",
            url=TOKEN_ENDPOINT,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=form,
            timeout=self._timeout,
        )
        if resp.status >= 400:
            raise GmailAuthError(
                f"gmail token exchange failed: HTTP {resp.status} {_error_detail(resp.body)}"
            )
        data = _safe_json(resp.body)
        token = data.get("access_token") if isinstance(data, dict) else None
        if not token:
            raise GmailAuthError("gmail token exchange returned no access_token")
        return str(token)

    @staticmethod
    def _build_raw_message(
        *,
        to: str,
        subject: str,
        body: str,
        from_addr: str | None,
        attachments: Sequence[Mapping[str, Any]] | None = None,
    ) -> tuple[str, tuple[AttachmentReceipt, ...]]:
        """Build an RFC822 message (shared :func:`build_mail_message` — validates any
        attachments FAIL-CLOSED) and base64url-encode it for the Gmail ``raw`` field.
        Returns ``(raw, attachment_receipts)``."""
        msg, receipts = build_mail_message(
            to=to, subject=subject, body=body, from_addr=from_addr, attachments=attachments
        )
        return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii"), receipts


def _safe_json(raw: bytes | str) -> dict:
    try:
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        out = json.loads(text)
        return out if isinstance(out, dict) else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _error_detail(raw: bytes | str) -> str:
    """Extract a provider error message for a connector error — never a token. The
    refresh/access token is only ever in the request we send, never in Google's
    error body, so echoing the provider's error text leaks no secret."""
    data = _safe_json(raw)
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("status") or err)
        if err:
            desc = data.get("error_description")
            return f"{err}{f': {desc}' if desc else ''}"
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    return text[:200]
