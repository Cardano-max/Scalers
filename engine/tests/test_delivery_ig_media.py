"""DELIVERY item 2 — per-action IG media (spec §11): honest URL resolution, no global-image lies.

IG's Graph API pulls the post image from a PUBLICLY reachable URL, so the publish
path must be honest about where that URL comes from:

* an explicit public URL staged on the action's ``context.artwork`` wins;
* a promised artifact id is served via ``PUBLIC_ASSET_BASE_URL``
  (``{base}/studio/artifacts/{id}/raw``) when configured;
* a promised artifact with NO public serving fails with the concrete
  "IG needs a publicly reachable image URL..." refusal — a draft that promised
  specific artwork is never silently published with different media;
* only a draft with NO per-action media at all may use the legacy global
  ``DEMO_IG_IMAGE_URL``, and that fallback is logged honestly.

All through a fake IG connector — no Graph call.
"""

from __future__ import annotations

import json
import logging

import pytest

import actions.publish as publish
from actions.publish import _IG_NO_PUBLIC_URL_ERROR, approve_and_publish
from actions.store import ActionRow

_TENANT = "test_delivery_tenant"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TEST_MODE_LEGACY_PASSTHROUGH", _TENANT)
    monkeypatch.delenv("DEMO_IG_IMAGE_URL", raising=False)
    monkeypatch.delenv("PUBLIC_ASSET_BASE_URL", raising=False)


class _FakeStore:
    def __init__(self, *rows: ActionRow) -> None:
        self.rows = {r.id: r for r in rows}

    def get_action(self, action_id, dsn=None):
        return self.rows.get(action_id)

    def update_status(self, action_id, status, *, dsn=None, **fields):
        row = self.rows[action_id]
        row.status = status
        for k, v in fields.items():
            setattr(row, k, v)
        return row

    def claim_for_send(self, action_id, *, dsn=None):
        import datetime as _dt

        row = self.rows.get(action_id)
        if row is None or row.status != "pending":
            return None
        row.status = "sending"
        row.autonomy = "approved"
        row.approved_at = _dt.datetime.now(_dt.timezone.utc)
        return row


class _FakeInstagram:
    def __init__(self, *, post_result=None, exc=None):
        self.calls: list[tuple] = []
        self._post, self._exc = post_result, exc

    def post(self, image_url, caption):
        self.calls.append((image_url, caption))
        if self._exc:
            raise self._exc
        import types

        return self._post or types.SimpleNamespace(
            media_id="mid_1", creation_id="c_1", permalink="https://www.instagram.com/p/x"
        )


@pytest.fixture
def patched_store(monkeypatch):
    def _install(*rows: ActionRow) -> _FakeStore:
        store = _FakeStore(*rows)
        monkeypatch.setattr(publish, "get_action", store.get_action)
        monkeypatch.setattr(publish, "update_status", store.update_status)
        monkeypatch.setattr(publish, "claim_for_send", store.claim_for_send)
        return store

    return _install


@pytest.fixture
def audit_rows(monkeypatch):
    rows: list[dict] = []
    monkeypatch.setattr(
        publish, "_record_send_audit_row",
        lambda action, **kw: rows.append({"action_id": action.id, **kw}),
    )
    return rows


def _pending(context=None, **kw) -> ActionRow:
    kw.setdefault("id", "act_ig1")
    kw.setdefault("tenant_id", _TENANT)
    kw.setdefault("type", "post")
    kw.setdefault("draft", "Fresh flash drop — link in bio")
    kw.setdefault("status", "pending")
    kw.setdefault("idempotency_key", "k-ig")
    return ActionRow(channel="instagram", context=context, **kw)


# ── resolution order ─────────────────────────────────────────────────────────────


def test_context_public_url_wins(patched_store, monkeypatch, audit_rows):
    # Even with the demo env var AND a base URL set, the draft's own staged public
    # URL is what gets published.
    monkeypatch.setenv("DEMO_IG_IMAGE_URL", "https://demo.example/global.jpg")
    monkeypatch.setenv("PUBLIC_ASSET_BASE_URL", "https://tunnel.example")
    ctx = json.dumps({
        "artwork": {
            "assetId": "as1", "artifactId": "art_3", "vlmSummary": "a rose",
            "publicUrl": "https://cdn.example/rose.jpg",
        },
        "attachment_artifact_id": "art_3",
    })
    patched_store(_pending(context=ctx))
    ig = _FakeInstagram()
    out = approve_and_publish("act_ig1", connectors={"instagram": ig})

    assert out.status == "sent"
    assert ig.calls == [("https://cdn.example/rose.jpg", "Fresh flash drop — link in bio")]
    sent = [a for a in audit_rows if a["result"] == "sent"]
    assert sent[0]["transport"] == "instagram-graph"
    assert sent[0]["provider_id"] == "mid_1"
    assert "context_url" in sent[0]["detail"]


def test_artifact_id_plus_public_asset_base_builds_raw_url(patched_store, monkeypatch):
    monkeypatch.setenv("PUBLIC_ASSET_BASE_URL", "https://tunnel.example/")  # trailing slash ok
    ctx = json.dumps({"artwork": {"artifactId": "art_42"}})
    patched_store(_pending(context=ctx))
    ig = _FakeInstagram()
    out = approve_and_publish("act_ig1", connectors={"instagram": ig})

    assert out.status == "sent"
    assert ig.calls[0][0] == "https://tunnel.example/studio/artifacts/art_42/raw"


def test_artifact_without_public_serving_fails_with_the_concrete_reason(patched_store, monkeypatch):
    # DEMO_IG_IMAGE_URL is set, but the draft promised SPECIFIC artwork — publishing
    # the global demo image instead would be dishonest. It must fail, not swap media.
    monkeypatch.setenv("DEMO_IG_IMAGE_URL", "https://demo.example/global.jpg")
    ctx = json.dumps({"attachment_artifact_id": "art_7"})
    patched_store(_pending(context=ctx))
    ig = _FakeInstagram()
    out = approve_and_publish("act_ig1", connectors={"instagram": ig})

    assert out.status == "failed"
    assert ig.calls == []  # nothing published
    assert _IG_NO_PUBLIC_URL_ERROR in out.last_error
    assert "art_7" in out.last_error


def test_no_per_action_media_falls_back_to_demo_env_with_honest_log(
    patched_store, monkeypatch, caplog
):
    monkeypatch.setenv("DEMO_IG_IMAGE_URL", "https://demo.example/global.jpg")
    patched_store(_pending(context=None))
    ig = _FakeInstagram()
    with caplog.at_level(logging.WARNING, logger="actions.publish"):
        out = approve_and_publish("act_ig1", connectors={"instagram": ig})

    assert out.status == "sent"
    assert ig.calls[0][0] == "https://demo.example/global.jpg"
    assert any("DEMO_IG_IMAGE_URL" in r.message and "fall" in r.message.lower()
               for r in caplog.records)


def test_no_media_at_all_fails_honestly(patched_store):
    patched_store(_pending(context=None))
    ig = _FakeInstagram()
    out = approve_and_publish("act_ig1", connectors={"instagram": ig})
    assert out.status == "failed"
    assert ig.calls == []
    assert "public image" in out.last_error


def test_non_http_context_url_is_ignored_not_published(patched_store):
    # A local path / data-uri staged as 'publicUrl' is NOT publicly reachable;
    # resolution ignores it and (with the artifact also unservable) fails honestly.
    ctx = json.dumps({"artwork": {"artifactId": "art_9", "publicUrl": "/var/artifacts/a.png"}})
    patched_store(_pending(context=ctx))
    ig = _FakeInstagram()
    out = approve_and_publish("act_ig1", connectors={"instagram": ig})
    assert out.status == "failed"
    assert _IG_NO_PUBLIC_URL_ERROR in out.last_error


def test_graph_error_with_per_action_media_marks_failed_with_real_error(
    patched_store, monkeypatch, audit_rows
):
    monkeypatch.setenv("PUBLIC_ASSET_BASE_URL", "https://tunnel.example")
    ctx = json.dumps({"attachment_artifact_id": "art_5"})
    patched_store(_pending(context=ctx))
    ig = _FakeInstagram(exc=RuntimeError("Graph OAuthException code 190 (expired)"))
    out = approve_and_publish("act_ig1", connectors={"instagram": ig})

    assert out.status == "failed"
    assert "190" in out.last_error
    failed = [a for a in audit_rows if a["result"] == "failed"]
    assert failed[0]["transport"] == "instagram-graph"
    assert "public_asset_base" in failed[0]["detail"]

# Whole module needs a live Postgres (ENGINE_DATABASE_URL): it runs in the CI
# integration lane (schema applied via initdb + bootstrap), not the DB-free unit lane.
pytestmark = pytest.mark.integration
