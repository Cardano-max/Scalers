"""approve_and_publish / reject tests — mock connectors, in-memory store, no PG.

Asserts connector selection by channel, exactly-once (a sent action is not
re-sent), and that a connector failure (or a missing connector) marks the action
``failed`` with the real error — never a fake success. The store layer is faked
in-memory so these run DB-free in the unit lane.
"""

from __future__ import annotations

import types

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

    def claim_for_send(self, action_id, *, dsn=None):
        """In-memory mirror of the atomic pending->'sending' claim.

        Returns the claimed row iff it was 'pending'; otherwise None (already
        claimed/sent/terminal) — exactly the DB ``RETURNING`` 1-row/0-row contract."""
        import datetime as _dt

        row = self.rows.get(action_id)
        if row is None or row.status != "pending":
            return None
        row.status = "sending"
        row.autonomy = "approved"
        row.approved_at = _dt.datetime.now(_dt.timezone.utc)
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


class _FakeInstagram:
    """Mirrors the real IG connector: post() + reply_to_comment()."""

    def __init__(self, *, post_result=None, reply_result=None, exc=None):
        self.calls: list[tuple] = []
        self._post, self._reply, self._exc = post_result, reply_result, exc

    def post(self, image_url, caption):
        self.calls.append(("post", image_url, caption))
        if self._exc:
            raise self._exc
        return self._post

    def reply_to_comment(self, comment_id, message):
        self.calls.append(("reply", comment_id, message))
        if self._exc:
            raise self._exc
        return self._reply


@pytest.fixture
def patched_store(monkeypatch):
    def _install(*rows: ActionRow) -> _FakeStore:
        store = _FakeStore(*rows)
        monkeypatch.setattr(publish, "get_action", store.get_action)
        monkeypatch.setattr(publish, "update_status", store.update_status)
        monkeypatch.setattr(publish, "claim_for_send", store.claim_for_send)
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


def test_instagram_post_without_image_fails_honestly(patched_store, monkeypatch):
    # IG is wired to the real connector now. A post with no public image source
    # fails HONESTLY with a clear blocker (no network touched), never a fake send.
    monkeypatch.delenv("DEMO_IG_IMAGE_URL", raising=False)
    patched_store(_pending(channel="instagram", type="post", id="act_ig"))
    out = approve_and_publish("act_ig")
    assert out.status == "failed"
    assert "jpeg" in out.last_error.lower() or "image" in out.last_error.lower()
    assert out.outcome_kind != "success"


def test_instagram_comment_reply_sent_via_connector(patched_store):
    patched_store(_pending(channel="instagram", type="comment", id="act_igr",
                           target="ig_comment:123", draft="thanks! dm us to book"))
    ig = _FakeInstagram(reply_result=types.SimpleNamespace(
        reply_id="r_999", comment_id="ig_comment:123"))
    out = approve_and_publish("act_igr", connectors={"instagram": ig})
    assert out.status == "sent"
    assert out.outcome_label == "Replied"
    assert ig.calls == [("reply", "ig_comment:123", "thanks! dm us to book")]
    assert out.deep_link == "r_999"


def test_instagram_post_real_error_marks_failed(patched_store, monkeypatch):
    # With an image set but the (real) connector raising the Graph error, the
    # action is marked failed with the real error — never a fabricated success.
    monkeypatch.setenv("DEMO_IG_IMAGE_URL", "https://example.com/x.jpg")
    patched_store(_pending(channel="instagram", type="post", id="act_igp", draft="cap"))
    ig = _FakeInstagram(exc=RuntimeError("Graph OAuthException code 190 (expired)"))
    out = approve_and_publish("act_igp", connectors={"instagram": ig})
    assert out.status == "failed"
    assert "190" in out.last_error
    assert out.outcome_kind != "success"


# ── real-only: a mock connector can never live-send (Slice-5) ────────────────────


class _MockGmail:
    """A connector that declares itself a mock (``is_mock = True``) like
    ``sideeffects.posting.MockPostingConnector``. It must be REFUSED on the live
    path before any send happens."""

    is_mock = True

    def __init__(self):
        self.calls: list[tuple] = []

    def send(self, to, subject, body, *, from_addr=None):
        self.calls.append((to, subject, body))  # must never be reached
        return types.SimpleNamespace(deep_link="dl")


def test_live_path_refuses_mock_connector_never_fake_send(patched_store):
    patched_store(_pending())
    mock = _MockGmail()
    out = approve_and_publish("act_test1", connectors={"gmail": mock})

    assert out.status == "failed"
    assert mock.calls == []  # the send was refused BEFORE any external effect
    assert "mock" in out.last_error.lower()
    assert out.outcome_kind != "success"


def test_real_test_fake_is_not_treated_as_mock(patched_store):
    # The unit-test fakes do NOT set is_mock, so the guard leaves them alone and
    # the normal send path runs (regression guard for the is_mock check).
    patched_store(_pending())
    gmail = _FakeGmail(result=GmailSendResult(message_id="m1", deep_link="dl"))
    out = approve_and_publish("act_test1", connectors={"gmail": gmail})
    assert out.status == "sent"
    assert len(gmail.calls) == 1


# ── live-send authorization (#11): operator-explicit live vs safe test-redirect ──


def test_live_true_sends_clean_to_real_recipient_even_with_redirect(patched_store, monkeypatch):
    # GMAIL_REDIRECT_TO is set (the safe default), but the operator EXPLICITLY
    # authorized a live send. The just-claimed row is marked 'studio_real_send' and the
    # gmail send reaches the REAL recipient with a CLEAN subject (no [TEST] marker).
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")
    store = patched_store(_pending())
    gmail = _FakeGmail(result=GmailSendResult(message_id="m1", deep_link="dl"))
    out = approve_and_publish("act_test1", connectors={"gmail": gmail}, live=True)

    assert gmail.calls == [("client@studio.example", "Your custom piece", "Hello from Ladies First")]
    assert out.status == "sent"
    assert out.mode == "live"
    # 'studio_real_send' is the single set-site for the live worker; it was marked on
    # the claimed row, which is why the allow-list let it bypass the redirect.
    assert store.rows["act_test1"].worker == "studio_real_send"


def test_default_redirects_with_test_marker_when_redirect_set(patched_store, monkeypatch):
    # No live authorization (default False): with GMAIL_REDIRECT_TO set the send is
    # rerouted to the operator inbox with a [TEST->real] subject. The worker is NOT
    # marked, so the safe redirect default stands.
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")
    store = patched_store(_pending())
    gmail = _FakeGmail(result=GmailSendResult(message_id="m1", deep_link="dl"))
    out = approve_and_publish("act_test1", connectors={"gmail": gmail})

    assert gmail.calls == [("ops@inbox.example", "[TEST->client@studio.example] Your custom piece", "Hello from Ladies First")]
    assert out.status == "sent"
    assert out.mode == "test_redirect"
    assert store.rows["act_test1"].worker != "studio_real_send"


def test_no_redirect_env_sends_clean_live_mode(patched_store, monkeypatch):
    # With no GMAIL_REDIRECT_TO configured at all, a send is live by construction
    # (clean subject, real recipient) and reports mode 'live'.
    monkeypatch.delenv("GMAIL_REDIRECT_TO", raising=False)
    patched_store(_pending())
    gmail = _FakeGmail(result=GmailSendResult(message_id="m1", deep_link="dl"))
    out = approve_and_publish("act_test1", connectors={"gmail": gmail})

    assert gmail.calls == [("client@studio.example", "Your custom piece", "Hello from Ladies First")]
    assert out.mode == "live"


def test_unresolved_placeholder_blocks_send_and_does_not_double_fire(patched_store, monkeypatch):
    # A draft that still carries an unresolved {{...}} token is REFUSED at the send
    # backstop: it is marked failed, no external send happens, and a retry never
    # double-fires (the claim is already terminal).
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")
    patched_store(_pending(draft="Hi {{unsubscribe}} — book now"))
    gmail = _FakeGmail(result=GmailSendResult(message_id="m1", deep_link="dl"))
    out = approve_and_publish("act_test1", connectors={"gmail": gmail})

    assert out.status == "failed"
    assert gmail.calls == []  # no external effect
    assert "placeholder" in out.last_error.lower()
    assert out.mode == "test_redirect"

    # Retry: the action is terminal (failed), so the claim returns nothing and no
    # second send fires — exactly-once holds through the placeholder backstop.
    again = approve_and_publish("act_test1", connectors={"gmail": gmail})
    assert again.status == "failed"
    assert gmail.calls == []


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
