"""FB connector tests (bead xeu) — gated, secure, no real network.

Verifies sec's merge conditions: disabled-by-default; DMs hard-escalate;
appsecret_proof on every call; token in BODY never URL + never logged; FB Page
feed-post path through the de6 boundary; webhook signature verify.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from connectors.base import ConnectorDisabledError, ConnectorHeldError, appsecret_proof, redact
from connectors.fb import FacebookConnector
from research.providers.firecrawl import HttpResponse


class _FakeFetcher:
    def __init__(self, body='{"id": "1789_456"}', status=200):
        self.calls = []
        self._body, self._status = body, status

    def request(self, *, method, ip, host, path, headers, body, timeout):
        self.calls.append({"method": method, "ip": ip, "host": host, "path": path,
                           "headers": headers, "body": body})
        return HttpResponse(status=self._status, body=self._body)


def _resolver(ip="93.184.216.34"):
    def r(host, port, *a, **k):
        return [(None, None, None, "", (ip, port))]
    return r


def _conn(**kw):
    kw.setdefault("page_token", "PAGE-TOKEN-xyz")
    kw.setdefault("app_secret", "APP-SECRET")
    kw.setdefault("page_id", "1789")
    kw.setdefault("fetcher", _FakeFetcher())
    kw.setdefault("resolver", _resolver())
    return FacebookConnector(enabled=True, **kw)


# ── gating ───────────────────────────────────────────────────────────────────


async def test_disabled_by_default_refuses():
    c = FacebookConnector(page_token="t", app_secret="s", page_id="1")  # enabled defaults False
    assert c.enabled is False
    with pytest.raises(ConnectorDisabledError):
        await c.send("k1", "facebook_feed", {"message": "hi"})


async def test_dm_channel_always_escalates_even_enabled():
    c = _conn()
    with pytest.raises(ConnectorHeldError):
        await c.send("k1", "instagram_dm", {"message": "hi"})


# ── the real FB Page feed-post path ──────────────────────────────────────────


async def test_feed_post_through_secure_boundary():
    fake = _FakeFetcher()
    c = _conn(fetcher=fake)
    res = await c.send("idem-99", "facebook_feed", {"message": "Spring flash drop 🌸"})
    call = fake.calls[0]
    assert call["host"] == "graph.facebook.com"          # official host
    assert call["ip"] == "93.184.216.34"                 # pinned IP
    assert call["path"] == "/v25.0/1789/feed"            # page feed endpoint
    assert call["headers"]["Idempotency-Key"] == "idem-99"
    sent = json.loads(call["body"])
    assert sent["message"] == "Spring flash drop 🌸"
    assert sent["access_token"] == "PAGE-TOKEN-xyz"       # token in BODY
    # appsecret_proof is correct HMAC-SHA256(app_secret, token)
    assert sent["appsecret_proof"] == hmac.new(
        b"APP-SECRET", b"PAGE-TOKEN-xyz", hashlib.sha256).hexdigest()
    assert res.external_id == "1789_456"
    assert res.deep_link and "1789_456" in res.deep_link


async def test_token_never_in_url_path():
    fake = _FakeFetcher()
    await _conn(fetcher=fake).send("k", "facebook_feed", {"message": "x"})
    assert "PAGE-TOKEN-xyz" not in fake.calls[0]["path"]


async def test_missing_creds_held():
    c = FacebookConnector(enabled=True, page_token="t", app_secret="s", page_id=None)
    with pytest.raises(ConnectorHeldError):
        await c.send("k", "facebook_feed", {"message": "x"})


# ── secret hygiene + appsecret_proof + webhook ───────────────────────────────


def test_repr_and_redact_never_leak_token():
    c = FacebookConnector(page_token="SUPERSECRETTOKEN", app_secret="s", page_id="1")
    assert "SUPERSECRETTOKEN" not in repr(c)
    assert "SUPERSECRETTOKEN" not in redact("SUPERSECRETTOKEN")


def test_appsecret_proof_is_hmac_sha256():
    assert appsecret_proof("secret", "token") == hmac.new(
        b"secret", b"token", hashlib.sha256).hexdigest()


def test_webhook_signature_verify():
    c = FacebookConnector(app_secret="APP-SECRET", page_token="t", page_id="1")
    body = b'{"entry": []}'
    good = "sha256=" + hmac.new(b"APP-SECRET", body, hashlib.sha256).hexdigest()
    assert c.verify_webhook(body, good) is True
    assert c.verify_webhook(body, "sha256=deadbeef") is False   # forged
    assert c.verify_webhook(body, None) is False                # missing
