"""Gmail connector tests — gated, stdlib-only, no real network.

Drives the connector through an injected fake transport and asserts it builds the
exact token-exchange + send requests (form body, base64url raw, Bearer header,
token never in a URL, never logged), honors the disabled gate, and surfaces the
real provider error instead of faking success. No network is touched.
"""

from __future__ import annotations

import base64
import json
import urllib.parse

import pytest

from connectors.base import ConnectorDisabledError, redact
from connectors.gmail import (
    SEND_ENDPOINT,
    TOKEN_ENDPOINT,
    GmailAuthError,
    GmailConnector,
    GmailSendError,
    GmailSendResult,
    HttpResult,
)


class _FakeTransport:
    """Records each request and returns scripted responses in order."""

    def __init__(self, *responses: HttpResult) -> None:
        self.calls: list[dict] = []
        self._responses = list(responses)

    def __call__(self, *, method, url, headers, body, timeout):
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers), "body": body, "timeout": timeout}
        )
        return self._responses[len(self.calls) - 1]


_TOKEN_OK = HttpResult(200, b'{"access_token": "ya29.FAKE-ACCESS", "expires_in": 3599}')
_SEND_OK = HttpResult(200, b'{"id": "1899abcdef0", "threadId": "1899thread"}')


def _conn(transport, **kw):
    kw.setdefault("client_id", "CID-123")
    kw.setdefault("client_secret", "CSECRET-xyz")
    kw.setdefault("refresh_token", "REFRESH-TOKEN-abc")
    return GmailConnector(transport=transport, enabled=True, **kw)


# ── gating ─────────────────────────────────────────────────────────────────────


def test_disabled_by_default_refuses():
    c = GmailConnector(client_id="a", client_secret="b", refresh_token="c")
    assert c.enabled is False
    with pytest.raises(ConnectorDisabledError):
        c.send("x@example.com", "subj", "body")


def test_missing_creds_raises_auth_error():
    c = GmailConnector(enabled=True)  # no creds
    with pytest.raises(GmailAuthError):
        c.send("x@example.com", "subj", "body")


# ── the real send path (mocked transport) ──────────────────────────────────────


def test_send_builds_token_exchange_then_send_request():
    fake = _FakeTransport(_TOKEN_OK, _SEND_OK)
    res = _conn(fake).send("client@studio.example", "Your custom piece", "Hi there 🌿")

    # 1) refresh -> access exchange: form-encoded body to the OAuth token endpoint.
    tok = fake.calls[0]
    assert tok["url"] == TOKEN_ENDPOINT
    assert tok["method"] == "POST"
    assert tok["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    form = urllib.parse.parse_qs(tok["body"].decode())
    assert form["grant_type"] == ["refresh_token"]
    assert form["client_id"] == ["CID-123"]
    assert form["client_secret"] == ["CSECRET-xyz"]
    assert form["refresh_token"] == ["REFRESH-TOKEN-abc"]
    # token material is in the BODY, never the URL.
    assert "REFRESH-TOKEN-abc" not in tok["url"]

    # 2) send: Bearer the freshly-exchanged access token, JSON {"raw": ...}.
    snd = fake.calls[1]
    assert snd["url"] == SEND_ENDPOINT
    assert snd["method"] == "POST"
    assert snd["headers"]["Authorization"] == "Bearer ya29.FAKE-ACCESS"
    assert snd["headers"]["Content-Type"] == "application/json"
    assert "ya29.FAKE-ACCESS" not in snd["url"]

    payload = json.loads(snd["body"])
    raw_bytes = base64.urlsafe_b64decode(payload["raw"].encode("ascii"))
    rfc822 = raw_bytes.decode("utf-8")
    assert "To: client@studio.example" in rfc822
    assert "Subject: Your custom piece" in rfc822
    assert "Hi there" in rfc822  # body present in the MIME part

    # result carries the gmail message id + a sent deep link.
    assert isinstance(res, GmailSendResult)
    assert res.message_id == "1899abcdef0"
    assert res.deep_link == "https://mail.google.com/mail/u/0/#sent/1899abcdef0"
    assert res.thread_id == "1899thread"


def test_from_addr_sets_from_header():
    fake = _FakeTransport(_TOKEN_OK, _SEND_OK)
    _conn(fake).send("c@x.example", "s", "b", from_addr="studio@ladies8391.example")
    raw = base64.urlsafe_b64decode(json.loads(fake.calls[1]["body"])["raw"].encode())
    assert "From: studio@ladies8391.example" in raw.decode()


# ── failure handling: the real error, never a fake success ──────────────────────


def test_token_exchange_failure_raises_auth_error():
    fake = _FakeTransport(HttpResult(400, b'{"error": "invalid_grant", "error_description": "expired"}'))
    with pytest.raises(GmailAuthError) as ei:
        _conn(fake).send("c@x.example", "s", "b")
    assert "invalid_grant" in str(ei.value)
    assert len(fake.calls) == 1  # never reached the send endpoint


def test_send_http_error_raises_send_error_not_success():
    fake = _FakeTransport(_TOKEN_OK, HttpResult(403, b'{"error": {"code": 403, "message": "insufficient scope"}}'))
    with pytest.raises(GmailSendError) as ei:
        _conn(fake).send("c@x.example", "s", "b")
    assert "403" in str(ei.value)
    assert "insufficient scope" in str(ei.value)


# ── secret hygiene ──────────────────────────────────────────────────────────────


def test_repr_and_logs_never_leak_secrets():
    c = GmailConnector(client_id="CID", client_secret="SUPERSECRET", refresh_token="REFRESH-SUPERSECRET")
    r = repr(c)
    assert "REFRESH-SUPERSECRET" not in r
    assert "SUPERSECRET" not in redact("SUPERSECRET")


def test_from_env_reads_key_from_env():
    env = {
        "GMAIL_CLIENT_ID": "env-cid",
        "GMAIL_CLIENT_SECRET": "env-secret",
        "LADIES8391_GMAIL_OAUTH_REFRESH": "env-refresh",
    }
    fake = _FakeTransport(_TOKEN_OK, _SEND_OK)
    c = GmailConnector.from_env(enabled=True, env=env, transport=fake)
    c.send("c@x.example", "s", "b")
    form = urllib.parse.parse_qs(fake.calls[0]["body"].decode())
    assert form["client_id"] == ["env-cid"]
    assert form["refresh_token"] == ["env-refresh"]
