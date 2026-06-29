"""approve_and_publish / reject tests — mock connectors, in-memory store, no PG.

Asserts connector selection by channel, exactly-once (a sent action is not
re-sent), and that a connector failure (or a missing connector) marks the action
``failed`` with the real error — never a fake success. The store layer is faked
in-memory so these run DB-free in the unit lane.
"""

from __future__ import annotations

import pytest

import actions.publish as publish
from actions.publish import ActionNotFoundError, approve_and_publish, reject
from actions.store import ActionRow
from connectors.gmail import GmailSendError, GmailSendResult
from sideeffects.provider import ProviderResult


class _FakeStore:
    """In-memory stand-in for actions.store.get_action / update_status."""

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


class _FakeGmail:
    def __init__(self, *, result=None, exc=None):
        self.calls: list[tuple] = []
        self._result, self._exc = result, exc

    def send(self, to, subject, body, *, from_addr=None):
        self.calls.append((to, subject, body))
        if self._exc:
            raise self._exc
        return self._result


class _FakeFacebook:
    """Mirrors the real async FB connector contract."""

    def __init__(self, *, result=None, exc=None):
        self.calls: list[tuple] = []
        self._result, self._exc = result, exc

    async def send(self, key, channel, payload):
        self.calls.append((key, channel, payload))
        if self._exc:
            raise self._exc
        return self._result


@pytest.fixture
def patched_store(monkeypatch):
    def _install(*rows: ActionRow) -> _FakeStore:
        store = _FakeStore(*rows)
        monkeypatch.setattr(publish, "get_action", store.get_action)
        monkeypatch.setattr(publish, "update_status", store.update_status)
        return store

    return _install


def _pending(channel="gmail", **kw) -> ActionRow:
    kw.setdefault("id", "act_test1")
    kw.setdefault("tenant_id", "ladies8391")
    kw.setdefault("type", "outreach")
    kw.setdefault("draft", "Hello from Ladies First")
    kw.setdefault("status", "pending")
    kw.setdefault("target", "client@studio.example")
    kw.setdefault("subject", "Your custom piece")
    kw.setdefault("idempotency_key", "ladies8391:gmail:client@studio.example:abc123")
    return ActionRow(channel=channel, **kw)


# ── gmail: real send + the sent fields ──────────────────────────────────────────


def test_gmail_approve_sends_and_marks_sent(patched_store):
    patched_store(_pending())
    gmail = _FakeGmail(result=GmailSendResult(
        message_id="m1", deep_link="https://mail.google.com/mail/u/0/#sent/m1"))
    out = approve_and_publish("act_test1", connectors={"gmail": gmail})

    assert gmail.calls == [("client@studio.example", "Your custom piece", "Hello from Ladies First")]
    assert out.status == "sent"
    assert out.deep_link == "https://mail.google.com/mail/u/0/#sent/m1"
    assert out.outcome_label == "Sent"
    assert out.outcome_kind == "success"
    assert out.autonomy == "approved"
    assert out.approved_at is not None and out.sent_at is not None


# ── exactly-once ────────────────────────────────────────────────────────────────


def test_exactly_once_does_not_resend_a_sent_action(patched_store):
    patched_store(_pending())
    gmail = _FakeGmail(result=GmailSendResult(message_id="m1", deep_link="dl"))

    first = approve_and_publish("act_test1", connectors={"gmail": gmail})
    assert first.status == "sent"
    second = approve_and_publish("act_test1", connectors={"gmail": gmail})  # replay

    assert second.status == "sent"
    assert len(gmail.calls) == 1  # the external effect happened exactly once


# ── failure handling: real error, never a fake success ──────────────────────────


def test_gmail_send_failure_marks_failed_with_real_error(patched_store):
    patched_store(_pending())
    gmail = _FakeGmail(exc=GmailSendError("gmail send failed: HTTP 403 insufficient scope"))
    out = approve_and_publish("act_test1", connectors={"gmail": gmail})

    assert out.status == "failed"
    assert "403" in out.last_error
    assert out.deep_link is None
    assert out.outcome_kind != "success"


def test_facebook_real_error_marks_failed_never_fake_success(patched_store):
    patched_store(_pending(channel="facebook", id="act_fb"))
    fb = _FakeFacebook(exc=RuntimeError("Graph API HTTP 400 on facebook_feed"))
    out = approve_and_publish("act_fb", connectors={"facebook": fb})

    assert out.status == "failed"
    assert "400" in out.last_error
    assert out.outcome_kind != "success"


def test_facebook_success_path_marks_published(patched_store):
    patched_store(_pending(channel="facebook", id="act_fb2"))
    fb = _FakeFacebook(result=ProviderResult(
        provider_id="1789_456", deep_link="https://www.facebook.com/1789_456"))
    out = approve_and_publish("act_fb2", connectors={"facebook": fb})

    assert out.status == "sent"
    assert out.outcome_label == "Published"
    assert out.deep_link == "https://www.facebook.com/1789_456"


def test_instagram_marks_failed_ig_pending(patched_store):
    patched_store(_pending(channel="instagram", id="act_ig"))
    out = approve_and_publish("act_ig")
    assert out.status == "failed"
    assert out.last_error == "ig connector pending"


def test_reject_marks_rejected_no_send(patched_store):
    patched_store(_pending(id="act_rej"))
    out = reject("act_rej")
    assert out.status == "rejected"


def test_unknown_action_raises(patched_store):
    patched_store()
    with pytest.raises(ActionNotFoundError):
        approve_and_publish("nope")
    with pytest.raises(ActionNotFoundError):
        reject("nope")
