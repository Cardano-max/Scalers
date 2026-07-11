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


@pytest.fixture(autouse=True)
def _legacy_passthrough_env(monkeypatch):
    # wwy.4: the fail-closed tenant gate refuses an unregistered tenant unless it
    # is explicitly allowlisted. These suites exercise unrelated send behavior for
    # the legacy 'ladies8391' tenant, which production lists in
    # TEST_MODE_LEGACY_PASSTHROUGH — declare the same here.
    monkeypatch.setenv("TEST_MODE_LEGACY_PASSTHROUGH", "ladies8391,test_safe_send")


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
    # An EXPLICIT live authorization reaches the real recipient with a clean
    # subject (the success path). The fail-closed default — a plain approve with
    # no redirect and no live auth — is covered by test_no_redirect_env_fails_closed.
    patched_store(_pending())
    gmail = _FakeGmail(result=GmailSendResult(
        message_id="m1", deep_link="https://mail.google.com/mail/u/0/#sent/m1"))
    out = approve_and_publish("act_test1", connectors={"gmail": gmail}, live=True)

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

    first = approve_and_publish("act_test1", connectors={"gmail": gmail}, live=True)
    assert first.status == "sent"
    second = approve_and_publish("act_test1", connectors={"gmail": gmail}, live=True)  # replay

    assert second.status == "sent"
    assert len(gmail.calls) == 1  # the external effect happened exactly once


# ── failure handling: real error, never a fake success ──────────────────────────


def test_gmail_send_failure_marks_failed_with_real_error(patched_store, monkeypatch):
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")  # safe path so the send is reached
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


# ── FB channel alias + staged-media resolution (page photo/video/text posts) ────


def test_fb_alias_hits_facebook_credential_gate_not_unknown_channel(patched_store, monkeypatch):
    # 'fb' rows (the campaign spine's Channel.FB value) must route to the FACEBOOK
    # credential gate — never fall through to "unknown channel 'fb'".
    for k in ("META_PAGE_TOKEN", "META_PAGE_ID"):
        monkeypatch.delenv(k, raising=False)
    patched_store(_pending(channel="fb", type="post", id="act_fbalias1"))
    with pytest.raises(publish.MetaCredentialsMissingError) as exc:
        approve_and_publish("act_fbalias1")
    assert "META_PAGE_TOKEN" in str(exc.value)
    assert "META_PAGE_ID" in str(exc.value)


def test_fb_alias_success_path_posts_text_to_feed(patched_store, monkeypatch):
    # An approved 'fb' draft with NO staged media publishes text-only through the
    # facebook connector ('facebook_feed', message-only payload) and stages 'sent'.
    monkeypatch.delenv("DEMO_IG_IMAGE_URL", raising=False)
    patched_store(_pending(channel="fb", type="post", id="act_fbalias2"))
    fb = _FakeFacebook(result=ProviderResult(
        provider_id="1789_777", deep_link="https://www.facebook.com/1789_777"))
    out = approve_and_publish("act_fbalias2", connectors={"facebook": fb})

    assert out.status == "sent"
    assert out.outcome_label == "Published"
    key, channel, payload = fb.calls[0]
    assert channel == "facebook_feed"
    assert payload == {"message": "Hello from Ladies First"}  # text-only: no media keys


def test_fb_with_staged_image_publishes_photo_payload(patched_store):
    import json as _json

    patched_store(_pending(
        channel="fb", type="post", id="act_fbimg",
        context=_json.dumps({"artwork": {"publicUrl": "https://img.example/peony.jpg"}}),
    ))
    fb = _FakeFacebook(result=ProviderResult(provider_id="1789_888"))
    out = approve_and_publish("act_fbimg", connectors={"facebook": fb})

    assert out.status == "sent"
    _key, _channel, payload = fb.calls[0]
    assert payload["image_url"] == "https://img.example/peony.jpg"
    assert "video_url" not in payload
    assert payload["message"] == "Hello from Ladies First"


def test_fb_with_staged_video_wins_over_image(patched_store):
    # Priority video > image: a reel-style staged asset is the primary media.
    import json as _json

    patched_store(_pending(
        channel="fb", type="post", id="act_fbvid",
        context=_json.dumps({"artwork": {
            "videoUrl": "https://vid.example/session.mp4",
            "publicUrl": "https://img.example/still.jpg",
        }}),
    ))
    fb = _FakeFacebook(result=ProviderResult(provider_id="1789_999"))
    out = approve_and_publish("act_fbvid", connectors={"facebook": fb})

    assert out.status == "sent"
    _key, _channel, payload = fb.calls[0]
    assert payload["video_url"] == "https://vid.example/session.mp4"
    assert "image_url" not in payload


def test_fb_promised_artifact_without_public_base_fails_honestly(patched_store, monkeypatch):
    # A draft that PROMISED specific artwork (artifact id) with no way to serve it
    # publicly fails with the concrete reason — NEVER a silent text-only downgrade.
    import json as _json

    monkeypatch.delenv("PUBLIC_ASSET_BASE_URL", raising=False)
    monkeypatch.delenv("DEMO_IG_IMAGE_URL", raising=False)
    patched_store(_pending(
        channel="fb", type="post", id="act_fbart",
        context=_json.dumps({"attachment_artifact_id": "artf_123"}),
    ))
    fb = _FakeFacebook(result=ProviderResult(provider_id="nope"))
    out = approve_and_publish("act_fbart", connectors={"facebook": fb})

    assert out.status == "failed"
    assert "artf_123" in (out.last_error or "")
    assert "not publicly served" in (out.last_error or "")
    assert fb.calls == []  # no publish attempted without the promised media


def test_fb_never_uses_the_global_demo_image_fallback(patched_store, monkeypatch):
    # DEMO_IG_IMAGE_URL is the IG demo fallback — a page post with no staged media
    # posts TEXT, it never publishes the global demo image in place of nothing.
    monkeypatch.setenv("DEMO_IG_IMAGE_URL", "https://demo.example/global.jpg")
    patched_store(_pending(channel="fb", type="post", id="act_fbdemo"))
    fb = _FakeFacebook(result=ProviderResult(provider_id="1789_000"))
    out = approve_and_publish("act_fbdemo", connectors={"facebook": fb})

    assert out.status == "sent"
    _key, _channel, payload = fb.calls[0]
    assert "image_url" not in payload and "video_url" not in payload


def test_instagram_post_without_image_fails_honestly(patched_store, monkeypatch):
    # With operator Meta credentials present (the ready-queue credential gate
    # passes), a post with no public image source still fails HONESTLY with a
    # clear blocker (no network touched), never a fake send.
    monkeypatch.delenv("DEMO_IG_IMAGE_URL", raising=False)
    monkeypatch.setenv("META_PAGE_TOKEN", "tok_unit_test")
    monkeypatch.setenv("META_IG_USER_ID", "17840000000000000")
    patched_store(_pending(channel="instagram", type="post", id="act_ig"))
    out = approve_and_publish("act_ig")
    assert out.status == "failed"
    assert "jpeg" in out.last_error.lower() or "image" in out.last_error.lower()
    assert out.outcome_kind != "success"


def test_instagram_post_without_meta_credentials_refuses_fail_closed(patched_store, monkeypatch):
    # Social ready queue: no operator Meta credentials → the approve REFUSES
    # BEFORE the exactly-once claim. The draft stays PENDING (waiting in the
    # ready queue) with the honest reason on last_error — never sent, never
    # failed-silent, no connector ever built.
    for key in ("META_PAGE_TOKEN", "META_IG_USER_ID", "META_PAGE_ID"):
        monkeypatch.delenv(key, raising=False)
    store = patched_store(_pending(channel="instagram", type="post", id="act_ig_nocred"))
    with pytest.raises(publish.MetaCredentialsMissingError) as ei:
        approve_and_publish("act_ig_nocred")
    assert "META_PAGE_TOKEN" in str(ei.value) and "META_IG_USER_ID" in str(ei.value)
    row = store.rows["act_ig_nocred"]
    assert row.status == "pending"  # not claimed — re-approvable once creds arrive
    assert "Meta credentials not configured" in (row.last_error or "")


def test_ig_alias_channel_hits_the_meta_gate_not_unknown_channel(patched_store, monkeypatch):
    # REAL campaign rows carry channel 'ig' (planner vocabulary). The alias must
    # fold into the instagram gate: no credentials → fail-closed refusal with the
    # instagram env keys named, row stays pending. Without the fold, 'ig' would
    # fall through to "unknown channel" and be marked failed — a silent loss of
    # a complete, waiting post package.
    for key in ("META_PAGE_TOKEN", "META_IG_USER_ID", "META_PAGE_ID"):
        monkeypatch.delenv(key, raising=False)
    store = patched_store(_pending(channel="ig", type="post", id="act_ig_alias"))
    with pytest.raises(publish.MetaCredentialsMissingError) as ei:
        approve_and_publish("act_ig_alias")
    assert "META_IG_USER_ID" in str(ei.value)
    row = store.rows["act_ig_alias"]
    assert row.status == "pending"
    assert "unknown channel" not in (row.last_error or "")


def test_comment_reply_is_exempt_from_meta_credential_gate(patched_store, monkeypatch):
    # An IG COMMENT REPLY publishes via the engagement reply connector (its own
    # env keys) — the META_* gate must NOT govern it: gating replies would block
    # working legacy-configured replies AND pass approves on keys the reply path
    # never reads. With no META creds and no injected connector, the approve must
    # reach the reply path (here: a mock connector that the real-only check
    # refuses → honest 'failed'), never MetaCredentialsMissingError / stuck
    # pending with a META reason it wouldn't actually hit.
    for key in ("META_PAGE_TOKEN", "META_IG_USER_ID", "META_PAGE_ID"):
        monkeypatch.delenv(key, raising=False)
    ig = _FakeInstagram(reply_result=types.SimpleNamespace(
        reply_id="r_gate", comment_id="ig_comment:77"))
    monkeypatch.setattr(publish, "_instagram_from_env", lambda: ig)
    store = patched_store(_pending(channel="instagram", type="comment", id="act_igr_gate",
                                   target="ig_comment:77", draft="thanks!"))
    out = approve_and_publish("act_igr_gate")  # would raise MetaCredentialsMissingError pre-fix
    row = store.rows["act_igr_gate"]
    # The reply path was genuinely reached (its connector was invoked)...
    assert ig.calls == [("reply", "ig_comment:77", "thanks!")]
    # ...and the row was never parked on a META reason it wouldn't actually hit.
    assert out.status != "pending" and row.status != "pending"
    assert "Meta credentials not configured" not in (row.last_error or "")


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


def test_live_path_refuses_mock_connector_never_fake_send(patched_store, monkeypatch):
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")  # reach the send path; mock still refused
    patched_store(_pending())
    mock = _MockGmail()
    out = approve_and_publish("act_test1", connectors={"gmail": mock})

    assert out.status == "failed"
    assert mock.calls == []  # the send was refused BEFORE any external effect
    assert "mock" in out.last_error.lower()
    assert out.outcome_kind != "success"


def test_real_test_fake_is_not_treated_as_mock(patched_store, monkeypatch):
    # The unit-test fakes do NOT set is_mock, so the guard leaves them alone and
    # the normal send path runs (regression guard for the is_mock check).
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")  # safe path so the send is reached
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


def test_no_redirect_env_fails_closed(patched_store, monkeypatch):
    # CRITICAL send-safety (wwy.3): with NO GMAIL_REDIRECT_TO and no explicit
    # live authorization, the send is REFUSED (fail closed) — a missing env var
    # can never turn a routine approve into live email. Was the accidental-send
    # bug (previously "live by construction").
    monkeypatch.delenv("GMAIL_REDIRECT_TO", raising=False)
    patched_store(_pending())
    gmail = _FakeGmail(result=GmailSendResult(message_id="m1", deep_link="dl"))
    out = approve_and_publish("act_test1", connectors={"gmail": gmail})

    assert gmail.calls == []  # never reached the network
    assert out.status == "failed"
    assert out.mode == "blocked"
    assert "GMAIL_REDIRECT_TO not configured" in (out.last_error or "")


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

# Whole module needs a live Postgres (ENGINE_DATABASE_URL): it runs in the CI
# integration lane (schema applied via initdb + bootstrap), not the DB-free unit lane.
pytestmark = pytest.mark.integration


# ── IG REELS channel (operator go-live): routing, honesty, publish ───────────


class _FakeInstagramReels(_FakeInstagram):
    def __init__(self, *, reel_result=None, exc=None, **kw):
        super().__init__(exc=exc, **kw)
        self._reel = reel_result

    def post_reel(self, video_url, caption):
        self.calls.append(("post_reel", video_url, caption))
        if self._exc:
            raise self._exc
        return self._reel


def test_reels_alias_hits_meta_gate_not_unknown_channel(patched_store, monkeypatch):
    # 'reels' rows must route to the instagram_reels credential gate — never fall
    # through to "unknown channel 'reels'".
    for k in ("META_PAGE_TOKEN", "META_IG_USER_ID"):
        monkeypatch.delenv(k, raising=False)
    patched_store(_pending(channel="reels", type="post", id="act_reel1"))
    with pytest.raises(publish.MetaCredentialsMissingError) as exc:
        approve_and_publish("act_reel1")
    assert "META_PAGE_TOKEN" in str(exc.value)
    assert "META_IG_USER_ID" in str(exc.value)


def test_reels_without_staged_video_fails_honestly(patched_store, monkeypatch):
    monkeypatch.setenv("META_PAGE_TOKEN", "tok_unit_test")
    monkeypatch.setenv("META_IG_USER_ID", "17840000000000000")
    patched_store(_pending(channel="reels", type="post", id="act_reel2"))
    reels = _FakeInstagramReels()
    out = approve_and_publish("act_reel2", connectors={"instagram": reels})
    assert out.status == "failed"
    assert "video" in (out.last_error or "").lower()
    assert reels.calls == []  # no publish attempted without media


def test_reels_with_staged_video_publishes_via_connector(patched_store, monkeypatch):
    monkeypatch.setenv("META_PAGE_TOKEN", "tok_unit_test")
    monkeypatch.setenv("META_IG_USER_ID", "17840000000000000")
    import json as _json

    from connectors.ig import InstagramPostResult

    patched_store(_pending(
        channel="reels", type="post", id="act_reel3",
        context=_json.dumps({"artwork": {"videoUrl": "https://vid.example/broll.mp4"}}),
    ))
    reels = _FakeInstagramReels(reel_result=InstagramPostResult(
        media_id="18000000000_reel", creation_id="17888_c",
        permalink="https://www.instagram.com/reel/XYZ/",
    ))
    out = approve_and_publish("act_reel3", connectors={"instagram": reels})
    assert out.status == "sent"
    assert out.outcome_label == "Published"
    assert out.deep_link == "https://www.instagram.com/reel/XYZ/"
    assert reels.calls and reels.calls[0][0] == "post_reel"
    assert reels.calls[0][1] == "https://vid.example/broll.mp4"


def test_publish_to_meta_builds_operator_ig_connector_from_env(monkeypatch):
    # The real activation path: publish_to_meta(channel='instagram') constructs an
    # ENABLED InstagramConnector from the operator META_* env keys and calls post.
    import connectors.ig as ig_mod

    captured: dict = {}

    class _RecordingConnector:
        def __init__(self, **kw):
            captured.update(kw)

        def post(self, image_url, caption):
            captured["posted"] = (image_url, caption)
            return "RESULT"

    monkeypatch.setattr(ig_mod, "InstagramConnector", _RecordingConnector)
    monkeypatch.setenv("META_PAGE_TOKEN", "tok_operator")
    monkeypatch.setenv("META_IG_USER_ID", "17841400361721657")
    monkeypatch.setenv("META_APP_SECRET", "sec_operator")

    row = _pending(channel="instagram", type="post", id="act_meta1")
    out = publish.publish_to_meta(row, channel="instagram", image_url="https://img.example/a.jpg")
    assert out == "RESULT"
    assert captured["enabled"] is True
    assert captured["ig_business_account_id"] == "17841400361721657"
    assert captured["page_token"] == "tok_operator"
    assert captured["app_secret"] == "sec_operator"
    assert captured["posted"] == ("https://img.example/a.jpg", row.draft)
