"""DELIVERY item 4 — the unified send-audit trail: ONE consistent row per publish attempt.

Every publish attempt — gmail API, gmail SMTP fallback, sandbox, IG post/reply,
FB post, and the post-claim refusals (blocked / placeholder / attachment) —
writes exactly one ``send_audit`` row with:

* ``kind='send'`` (additive next to the campaign-level 'send_eligible'/'override'),
* ``mode`` — 'live' | 'test_redirect' | 'blocked' | 'sandbox',
* ``reason`` — compact JSON: ``transport`` ('gmail-api' | 'gmail-smtp-fallback' |
  'sandbox' | 'instagram-graph' | 'facebook-graph'), the provider id, attachment
  receipts (filename + sha256 prefix — never content), and a failure detail,
* ``result`` — the action status after the attempt.

Unit lane captures the row kwargs via a monkeypatched ``record_send_audit``;
one PG test proves the row round-trips through the real ``send_audit`` table.
"""

from __future__ import annotations

import json
import types
import uuid

import pytest

import actions.audit as audit_mod
import actions.publish as publish
from actions.publish import approve_and_publish
from actions.store import ActionRow
from connectors.gmail import GmailSendResult

_TENANT = "test_delivery_tenant"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TEST_MODE_LEGACY_PASSTHROUGH", _TENANT)
    monkeypatch.delenv("SMTP_SENDER", raising=False)
    monkeypatch.delenv("SMTP_APP_PASSWORD", raising=False)


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
def captured(monkeypatch):
    """Capture the REAL record_send_audit kwargs (the actual row shape), no DB."""
    rows: list[dict] = []
    monkeypatch.setattr(
        audit_mod, "record_send_audit", lambda **kw: rows.append(kw) or "aud_x"
    )
    return rows


def _pending(channel="gmail", **kw) -> ActionRow:
    kw.setdefault("id", f"act_{channel}_aud")
    kw.setdefault("tenant_id", _TENANT)
    kw.setdefault("type", "outreach")
    kw.setdefault("draft", "Hello there")
    kw.setdefault("status", "pending")
    kw.setdefault("target", "client@studio.example")
    kw.setdefault("subject", "Hi")
    kw.setdefault("idempotency_key", f"k-{channel}")
    kw.setdefault("run_id", "run_aud_1")
    return ActionRow(channel=channel, **kw)


def _send_rows(captured):
    return [r for r in captured if r.get("kind") == "send"]


def _note(row) -> dict:
    note = json.loads(row["reason"])
    assert isinstance(note, dict)
    return note


class _Gmail:
    def __init__(self, result=None, exc=None):
        self._result, self._exc = result, exc

    def send(self, to, subject, body, *, from_addr=None, attachments=None):
        if self._exc:
            raise self._exc
        return self._result


def test_gmail_send_one_consistent_row(patched_store, monkeypatch, captured):
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")
    patched_store(_pending())
    approve_and_publish(
        "act_gmail_aud",
        connectors={"gmail": _Gmail(GmailSendResult(message_id="m9", deep_link="dl"))},
    )
    (row,) = _send_rows(captured)
    assert row["action_id"] == "act_gmail_aud"
    assert row["run_id"] == "run_aud_1" and row["tenant_id"] == _TENANT
    assert row["mode"] == "test_redirect"
    assert row["result"] == "sent"
    note = _note(row)
    assert note["transport"] == "gmail-api"
    assert note["provider_id"] == "m9"


def test_gmail_blocked_failclosed_is_audited(patched_store, monkeypatch, captured):
    monkeypatch.delenv("GMAIL_REDIRECT_TO", raising=False)
    patched_store(_pending(id="act_blocked"))
    approve_and_publish("act_blocked", connectors={"gmail": _Gmail()})
    (row,) = _send_rows(captured)
    assert row["mode"] == "blocked" and row["result"] == "failed"
    assert "GMAIL_REDIRECT_TO not configured" in _note(row)["detail"]


def test_sandbox_delivery_is_audited_as_sandbox(patched_store, captured):
    patched_store(_pending(channel="demo", id="act_demo_aud"))
    out = approve_and_publish("act_demo_aud")
    assert out.status == "sent"
    (row,) = _send_rows(captured)
    assert row["mode"] == "sandbox" and row["result"] == "sent"
    note = _note(row)
    assert note["transport"] == "sandbox"
    assert note["provider_id"].startswith("sandbox://demo/")


def test_instagram_post_and_reply_are_audited(patched_store, monkeypatch, captured):
    monkeypatch.setenv("DEMO_IG_IMAGE_URL", "https://demo.example/x.jpg")
    ig = types.SimpleNamespace(
        post=lambda image_url, caption: types.SimpleNamespace(
            media_id="mid_7", creation_id="c", permalink="p"
        ),
        reply_to_comment=lambda comment_id, message: types.SimpleNamespace(
            reply_id="r_5", comment_id=comment_id
        ),
    )
    patched_store(
        _pending(channel="instagram", type="post", id="act_igp_aud"),
        _pending(channel="instagram", type="comment", id="act_igr_aud", target="cm_1"),
    )
    approve_and_publish("act_igp_aud", connectors={"instagram": ig})
    approve_and_publish("act_igr_aud", connectors={"instagram": ig})
    rows = _send_rows(captured)
    assert len(rows) == 2
    by_action = {r["action_id"]: r for r in rows}
    assert _note(by_action["act_igp_aud"])["provider_id"] == "mid_7"
    assert _note(by_action["act_igr_aud"])["provider_id"] == "r_5"
    assert all(_note(r)["transport"] == "instagram-graph" for r in rows)
    assert all(r["mode"] == "live" and r["result"] == "sent" for r in rows)


def test_facebook_failure_is_audited_with_detail(patched_store, captured):
    class _FB:
        async def send(self, key, channel, payload):
            raise RuntimeError("Graph API HTTP 400 on facebook_feed")

    patched_store(_pending(channel="facebook", id="act_fb_aud"))
    approve_and_publish("act_fb_aud", connectors={"facebook": _FB()})
    (row,) = _send_rows(captured)
    assert row["mode"] == "live" and row["result"] == "failed"
    note = _note(row)
    assert note["transport"] == "facebook-graph"
    assert "400" in note["detail"]


def test_every_channel_row_shares_the_same_shape(patched_store, monkeypatch, captured):
    # The consistency claim itself: whatever the channel/outcome, a kind='send'
    # row always carries a mode, a transport, and a result.
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")
    monkeypatch.setenv("DEMO_IG_IMAGE_URL", "https://demo.example/x.jpg")
    ig = types.SimpleNamespace(
        post=lambda image_url, caption: types.SimpleNamespace(media_id="m", creation_id="c", permalink="p")
    )
    patched_store(
        _pending(id="a1"),
        _pending(channel="demo", id="a2"),
        _pending(channel="instagram", type="post", id="a3"),
    )
    approve_and_publish("a1", connectors={"gmail": _Gmail(GmailSendResult(message_id="m", deep_link="d"))})
    approve_and_publish("a2")
    approve_and_publish("a3", connectors={"instagram": ig})
    rows = _send_rows(captured)
    assert len(rows) == 3
    for r in rows:
        assert r["mode"] in {"live", "test_redirect", "blocked", "sandbox"}
        assert r["result"] in {"sent", "failed"}
        assert _note(r)["transport"] in {
            "gmail-api", "gmail-smtp-fallback", "sandbox", "instagram-graph", "facebook-graph",
        }


# ── PG round-trip: the row really lands in send_audit ────────────────────────────


def _pg_available() -> bool:
    try:
        import psycopg

        with psycopg.connect(
            "postgresql://scalers:scalers@localhost:5432/scalers", connect_timeout=2
        ):
            return True
    except Exception:
        return False


@pytest.mark.skipif(not _pg_available(), reason="postgres not reachable")
def test_send_audit_row_roundtrips_through_postgres():
    from actions.audit import list_send_audit
    from connectors.mail_message import validate_attachments

    dsn = "postgresql://scalers:scalers@localhost:5432/scalers"
    action = _pending(id=f"act_pgaud_{uuid.uuid4().hex[:8]}")
    _, receipts = validate_attachments(
        [{"filename": "art.png", "content_bytes": b"png-bytes", "mime_type": "image/png"}]
    )
    publish._record_send_audit_row(
        action, mode="test_redirect", result="sent", transport="gmail-api",
        provider_id="m123", attachments=receipts, dsn=dsn,
    )
    rows = list_send_audit(action_id=action.id, dsn=dsn)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "send" and row["mode"] == "test_redirect" and row["result"] == "sent"
    note = json.loads(row["reason"])
    assert note["transport"] == "gmail-api" and note["provider_id"] == "m123"
    assert "art.png" in note["attachments"][0]
    assert receipts[0].sha256_prefix in note["attachments"][0]
