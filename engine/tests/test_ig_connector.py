"""Instagram content-publish connector tests (Scalers item #3) — gated, no network.

Verifies the same merge contract sec applied to ``fb.py``: disabled-by-default;
the two-step IG Content Publishing request SHAPES (``/media`` then ``/media_publish``)
plus the comment-reply endpoint; ``appsecret_proof`` on every call; the page token
in the BODY / Authorization header, NEVER the URL path, NEVER logged; and a real
Graph error surfaced (never a fabricated success). No real network is touched — an
injected fake fetcher records every request and returns scripted responses.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from connectors.base import ConnectorDisabledError, redact
from connectors.ig import (
    InstagramConfigError,
    InstagramConnector,
    InstagramPublishError,
    InstagramReplyError,
)
from research.providers.firecrawl import HttpResponse

_IG_ID = "17841400000000000"
_PAGE_TOKEN = "PAGE-TOKEN-xyz"
_APP_SECRET = "APP-SECRET"
_PERMALINK = "https://www.instagram.com/p/ABC123def/"


class _FakeFetcher:
    """Records every request and routes a scripted response by endpoint. ``status``
    forces a non-2xx (with a real-shaped Graph error body) on EVERY call."""

    def __init__(self, *, status: int = 200, error_body: str | None = None):
        self.calls: list[dict] = []
        self._status = status
        self._error_body = error_body

    def request(self, *, method, ip, host, path, headers, body, timeout):
        self.calls.append(
            {"method": method, "ip": ip, "host": host, "path": path,
             "headers": headers, "body": body}
        )
        if self._status >= 400:
            return HttpResponse(status=self._status, body=self._error_body or "{}")
        if "/replies" in path:
            return HttpResponse(status=200, body='{"id": "17900000000_reply"}')
        if "media_publish" in path:
            return HttpResponse(status=200, body='{"id": "17999000000_media"}')
        if "permalink" in path:
            return HttpResponse(
                status=200,
                body=json.dumps({"id": "17999000000_media", "permalink": _PERMALINK}),
            )
        # the media container (step 1)
        return HttpResponse(status=200, body='{"id": "17888000000_creation"}')


def _resolver(ip="93.184.216.34"):
    def r(host, port, *a, **k):
        return [(None, None, None, "", (ip, port))]
    return r


def _conn(**kw):
    kw.setdefault("ig_business_account_id", _IG_ID)
    kw.setdefault("page_token", _PAGE_TOKEN)
    kw.setdefault("app_secret", _APP_SECRET)
    kw.setdefault("fetcher", _FakeFetcher())
    kw.setdefault("resolver", _resolver())
    return InstagramConnector(enabled=True, **kw)


def _expected_proof() -> str:
    return hmac.new(_APP_SECRET.encode(), _PAGE_TOKEN.encode(), hashlib.sha256).hexdigest()


# ── gating ───────────────────────────────────────────────────────────────────


def test_disabled_by_default_refuses_post():
    c = InstagramConnector(
        ig_business_account_id=_IG_ID, page_token="t", app_secret="s"
    )  # enabled defaults False
    assert c.enabled is False
    with pytest.raises(ConnectorDisabledError):
        c.post("https://img.example/photo.jpg", "caption")


def test_disabled_by_default_refuses_reply():
    c = InstagramConnector(ig_business_account_id=_IG_ID, page_token="t", app_secret="s")
    with pytest.raises(ConnectorDisabledError):
        c.reply_to_comment("17900000000", "thanks!")


def test_missing_creds_is_config_error():
    c = InstagramConnector(enabled=True, page_token="t", app_secret="s")  # no ig id
    with pytest.raises(InstagramConfigError):
        c.post("https://img.example/photo.jpg", "caption")


# ── the two-step IG Content Publishing flow ──────────────────────────────────


def test_two_step_publish_request_shapes():
    fake = _FakeFetcher()
    res = _conn(fetcher=fake).post("https://img.example/spring.jpg", "Spring drop 🌸")

    # exactly three calls: create container, publish, permalink fetch.
    assert len(fake.calls) == 3
    create, publish, permalink = fake.calls

    # step 1 — POST /v25.0/{ig-id}/media with image_url + caption in the BODY.
    assert create["method"] == "POST"
    assert create["host"] == "graph.facebook.com"          # official host
    assert create["ip"] == "93.184.216.34"                 # pinned IP
    assert create["path"] == f"/v25.0/{_IG_ID}/media"
    sent = json.loads(create["body"])
    assert sent["image_url"] == "https://img.example/spring.jpg"
    assert sent["caption"] == "Spring drop 🌸"
    assert sent["access_token"] == _PAGE_TOKEN              # token in BODY
    assert sent["appsecret_proof"] == _expected_proof()

    # step 2 — POST /v25.0/{ig-id}/media_publish carrying the creation_id.
    assert publish["method"] == "POST"
    assert publish["path"] == f"/v25.0/{_IG_ID}/media_publish"
    pub = json.loads(publish["body"])
    assert pub["creation_id"] == "17888000000_creation"
    assert pub["access_token"] == _PAGE_TOKEN
    assert pub["appsecret_proof"] == _expected_proof()

    # step 3 — GET permalink; token in the Authorization header, never the URL.
    assert permalink["method"] == "GET"
    assert permalink["path"].startswith("/v25.0/17999000000_media?fields=permalink")
    assert permalink["headers"]["Authorization"] == f"Bearer {_PAGE_TOKEN}"

    # the returned result carries {media_id, permalink, creation_id}.
    assert res.creation_id == "17888000000_creation"
    assert res.media_id == "17999000000_media"
    assert res.permalink == _PERMALINK


def test_token_never_in_any_url_path():
    fake = _FakeFetcher()
    _conn(fetcher=fake).post("https://img.example/x.jpg", "x")
    for call in fake.calls:
        assert _PAGE_TOKEN not in call["path"]


def test_publish_carries_real_graph_error_no_fake_success():
    # The real Meta token is expired today → Graph returns the real OAuth error.
    real_error = json.dumps(
        {"error": {"message": "Error validating access token: Session has expired",
                   "type": "OAuthException", "code": 190}}
    )
    fake = _FakeFetcher(status=400, error_body=real_error)
    with pytest.raises(InstagramPublishError) as exc:
        _conn(fetcher=fake).post("https://img.example/x.jpg", "x")
    msg = str(exc.value)
    assert "190" in msg
    assert "Session has expired" in msg          # the REAL provider error is carried
    assert _PAGE_TOKEN not in msg                # but never the token
    # it failed at the FIRST step — no media_publish was attempted.
    assert len(fake.calls) == 1
    assert fake.calls[0]["path"].endswith("/media")


# ── the engagement / auto-reply path ─────────────────────────────────────────


def test_reply_to_comment_endpoint_shape():
    fake = _FakeFetcher()
    res = _conn(fetcher=fake).reply_to_comment("17900000000", "Thanks for the love!")
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/v25.0/17900000000/replies"
    body = json.loads(call["body"])
    assert body["message"] == "Thanks for the love!"
    assert body["access_token"] == _PAGE_TOKEN
    assert body["appsecret_proof"] == _expected_proof()
    assert res.reply_id == "17900000000_reply"
    assert res.comment_id == "17900000000"


def test_reply_carries_real_graph_error():
    fake = _FakeFetcher(
        status=400,
        error_body=json.dumps({"error": {"message": "Unsupported request", "code": 100}}),
    )
    with pytest.raises(InstagramReplyError):
        _conn(fetcher=fake).reply_to_comment("17900000000", "hi")


# ── secret hygiene ───────────────────────────────────────────────────────────


def test_repr_never_leaks_token():
    c = InstagramConnector(
        ig_business_account_id=_IG_ID, page_token="SUPERSECRETTOKEN", app_secret="s"
    )
    assert "SUPERSECRETTOKEN" not in repr(c)
    assert "SUPERSECRETTOKEN" not in redact("SUPERSECRETTOKEN")


def test_from_env_reads_key_from_env():
    env = {
        "IG_BUSINESS_ACCOUNT_ID": _IG_ID,
        "LADIES8391_FB_PAGE_TOKEN": _PAGE_TOKEN,
        "META_APP_SECRET": _APP_SECRET,
    }
    c = InstagramConnector.from_env(
        env=env, enabled=True, fetcher=_FakeFetcher(), resolver=_resolver()
    )
    res = c.post("https://img.example/x.jpg", "x")
    assert res.media_id == "17999000000_media"


def test_from_env_falls_back_to_meta_access_token():
    env = {
        "IG_BUSINESS_ACCOUNT_ID": _IG_ID,
        "LADIES8391_META_ACCESS_TOKEN": "FALLBACK-TOKEN",
        "META_APP_SECRET": _APP_SECRET,
    }
    c = InstagramConnector.from_env(env=env)
    assert "FALLBACK-TOKEN" not in repr(c)  # still redacted


# ── the async REELS publish flow (operator go-live) ──────────────────────────


class _ReelsFetcher:
    """Scripted responses for the reels container flow; ``statuses`` is the
    sequence the status_code poll walks through."""

    def __init__(self, statuses: list[str]):
        self.calls: list[dict] = []
        self._statuses = list(statuses)

    def request(self, *, method, ip, host, path, headers, body, timeout):
        self.calls.append({"method": method, "path": path, "headers": headers, "body": body})
        if "status_code" in path:
            status = self._statuses.pop(0) if self._statuses else "IN_PROGRESS"
            return HttpResponse(status=200, body=json.dumps({"status_code": status}))
        if "media_publish" in path:
            return HttpResponse(status=200, body='{"id": "18000000000_reel"}')
        if "permalink" in path:
            return HttpResponse(
                status=200,
                body=json.dumps({"id": "18000000000_reel", "permalink": _PERMALINK}),
            )
        return HttpResponse(status=200, body='{"id": "17888000000_creation"}')


def test_reels_publish_flow_shapes_and_result():
    fake = _ReelsFetcher(["IN_PROGRESS", "FINISHED"])
    res = _conn(fetcher=fake).post_reel(
        "https://vid.example/broll.mp4", "B-roll reveal 🎬", poll_seconds=0
    )
    create = fake.calls[0]
    sent = json.loads(create["body"])
    assert create["path"] == f"/v25.0/{_IG_ID}/media"
    assert sent["media_type"] == "REELS"
    assert sent["video_url"] == "https://vid.example/broll.mp4"
    assert sent["access_token"] == _PAGE_TOKEN and sent["appsecret_proof"] == _expected_proof()
    # status polls carry the token in the Authorization header, never the URL.
    polls = [c for c in fake.calls if "status_code" in c["path"]]
    assert len(polls) == 2
    assert all(c["method"] == "GET" for c in polls)
    assert all(c["headers"]["Authorization"] == f"Bearer {_PAGE_TOKEN}" for c in polls)
    assert all(_PAGE_TOKEN not in c["path"] for c in fake.calls)
    publish = [c for c in fake.calls if "media_publish" in c["path"]]
    assert len(publish) == 1
    assert res.media_id == "18000000000_reel"
    assert res.permalink == _PERMALINK


def test_reels_container_error_raises_real_reason():
    fake = _ReelsFetcher(["ERROR"])
    with pytest.raises(InstagramPublishError) as exc:
        _conn(fetcher=fake).post_reel("https://vid.example/broll.mp4", "cap", poll_seconds=0)
    assert "ERROR" in str(exc.value)
    # publish must never have been attempted after a container error.
    assert not [c for c in fake.calls if "media_publish" in c["path"]]


def test_reels_poll_timeout_is_honest_not_published():
    fake = _ReelsFetcher([])  # every poll reads IN_PROGRESS
    with pytest.raises(InstagramPublishError) as exc:
        _conn(fetcher=fake).post_reel(
            "https://vid.example/broll.mp4", "cap", poll_seconds=0, max_polls=3
        )
    assert "not ready" in str(exc.value)
    assert not [c for c in fake.calls if "media_publish" in c["path"]]
