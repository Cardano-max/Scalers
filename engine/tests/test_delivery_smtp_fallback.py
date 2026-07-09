"""DELIVERY item 1b — SMTP app-password fallback ("Option B"): gated, fail-closed, audited.

The Gmail OAuth refresh token is dead (``invalid_grant``); the operator chose an
SMTP app-password fallback. These tests prove, with a FAKE SMTP client (no live
send, no secret values):

* :class:`connectors.smtp_mail.SmtpMailConnector` — disabled by default, refuses
  without ``SMTP_SENDER``/``SMTP_APP_PASSWORD``, logs in and submits the shared
  RFC822 message (attachments included, same validator as the Gmail API leg),
  never leaks the app password in repr/errors, surfaces the real smtplib error;
* :mod:`actions.publish` fallback selection — Gmail-API leg tried first; ONLY an
  auth-dead failure (``invalid_grant`` / creds absent) with SMTP configured falls
  back; the fallback runs AFTER (and therefore under) the same gate stack
  (redirect/[TEST] marker, placeholder refusal, TEST-MODE tenant gate,
  exactly-once claim), and the audit records ``transport=gmail-smtp-fallback``.
  No SMTP configured → the original concrete Gmail failure, never silent.
"""

from __future__ import annotations

import json

import pytest

import actions.publish as publish
from actions.publish import approve_and_publish
from actions.store import ActionRow
from connectors.base import ConnectorDisabledError
from connectors.gmail import GmailAuthError, GmailSendError, GmailSendResult
from connectors.mail_message import MailAttachmentError
from connectors.smtp_mail import (
    SmtpConfigError,
    SmtpMailConnector,
    SmtpSendError,
    SmtpSendResult,
)

_PNG = b"\x89PNG\r\n\x1a\nfake"
_TENANT = "test_delivery_tenant"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TEST_MODE_LEGACY_PASSTHROUGH", _TENANT)
    # No ambient SMTP creds unless a test sets them explicitly.
    monkeypatch.delenv("SMTP_SENDER", raising=False)
    monkeypatch.delenv("SMTP_APP_PASSWORD", raising=False)


# ── the fake SMTP client / factory ───────────────────────────────────────────────


class _FakeSmtpClient:
    def __init__(self, *, login_exc=None, send_exc=None, refused=None):
        self.logins: list[tuple] = []
        self.sent: list = []
        self.quit_called = False
        self._login_exc, self._send_exc, self._refused = login_exc, send_exc, refused

    def login(self, user, password):
        self.logins.append((user, password))
        if self._login_exc:
            raise self._login_exc

    def send_message(self, msg):
        if self._send_exc:
            raise self._send_exc
        self.sent.append(msg)
        return self._refused or {}

    def quit(self):
        self.quit_called = True


class _FakeSmtpFactory:
    def __init__(self, client: _FakeSmtpClient):
        self.client = client
        self.calls: list[tuple] = []

    def __call__(self, host, port, timeout):
        self.calls.append((host, port, timeout))
        return self.client


def _conn(client=None, **kw) -> tuple[SmtpMailConnector, _FakeSmtpFactory]:
    client = client or _FakeSmtpClient()
    factory = _FakeSmtpFactory(client)
    kw.setdefault("sender", "studio@ladies8391.example")
    kw.setdefault("app_password", "app-pass-SECRET")
    conn = SmtpMailConnector(smtp_factory=factory, enabled=True, **kw)
    return conn, factory


# ── connector: gating + creds fail-closed ────────────────────────────────────────


def test_disabled_by_default_refuses():
    c = SmtpMailConnector(sender="a@x.example", app_password="p")
    assert c.enabled is False
    with pytest.raises(ConnectorDisabledError):
        c.send("x@example.com", "s", "b")


def test_missing_creds_raise_config_error_never_silent():
    c = SmtpMailConnector(enabled=True)  # no creds
    with pytest.raises(SmtpConfigError, match="SMTP_SENDER/SMTP_APP_PASSWORD"):
        c.send("x@example.com", "s", "b")


def test_configured_in_env_checks_both_keys():
    assert SmtpMailConnector.configured_in_env({}) is False
    assert SmtpMailConnector.configured_in_env({"SMTP_SENDER": "a@x"}) is False
    assert SmtpMailConnector.configured_in_env(
        {"SMTP_SENDER": "a@x", "SMTP_APP_PASSWORD": " "}
    ) is False
    assert SmtpMailConnector.configured_in_env(
        {"SMTP_SENDER": "a@x", "SMTP_APP_PASSWORD": "p"}
    ) is True


# ── connector: the real submit path via the fake client ─────────────────────────


def test_send_logs_in_and_submits_message_with_message_id():
    conn, factory = _conn()
    res = conn.send("c@x.example", "Your piece", "hello body")

    assert factory.calls == [("smtp.gmail.com", 465, 15.0)]
    assert factory.client.logins == [("studio@ladies8391.example", "app-pass-SECRET")]
    (msg,) = factory.client.sent
    assert msg["To"] == "c@x.example"
    assert msg["Subject"] == "Your piece"
    assert msg["From"] == "studio@ladies8391.example"  # default From = the sender
    assert msg["Message-ID"] == res.message_id and res.message_id.startswith("<")
    assert "hello body" in msg.get_content()
    assert factory.client.quit_called  # connection closed even on success
    assert isinstance(res, SmtpSendResult)
    assert res.deep_link is None and res.transport == "smtp"


def test_send_with_attachment_shares_the_mail_message_validator():
    conn, factory = _conn()
    res = conn.send(
        "c@x.example", "s", "b",
        attachments=[{"filename": "art.png", "content_bytes": _PNG, "mime_type": "image/png"}],
    )
    (msg,) = factory.client.sent
    parts = list(msg.iter_attachments())
    assert [p.get_filename() for p in parts] == ["art.png"]
    assert parts[0].get_content() == _PNG
    assert [r.filename for r in res.attachments] == ["art.png"]


def test_invalid_attachment_refuses_before_any_connection():
    conn, factory = _conn()
    with pytest.raises(MailAttachmentError):
        conn.send(
            "c@x.example", "s", "b",
            attachments=[{"filename": "x.gif", "content_bytes": b"g", "mime_type": "image/gif"}],
        )
    assert factory.calls == []  # never even built the SMTP client — fail closed


def test_login_failure_surfaces_real_error_scrubbed():
    import smtplib

    client = _FakeSmtpClient(
        login_exc=smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")
    )
    conn, factory = _conn(client)
    with pytest.raises(SmtpSendError) as ei:
        conn.send("c@x.example", "s", "b")
    assert "535" in str(ei.value)
    assert "app-pass-SECRET" not in str(ei.value)
    assert factory.client.quit_called  # closed even on failure


def test_refused_recipient_is_a_failure_not_a_success():
    client = _FakeSmtpClient(refused={"c@x.example": (550, b"mailbox unavailable")})
    conn, _ = _conn(client)
    with pytest.raises(SmtpSendError, match="refused recipient"):
        conn.send("c@x.example", "s", "b")


def test_repr_never_leaks_the_app_password():
    conn, _ = _conn()
    assert "app-pass-SECRET" not in repr(conn)


def test_from_env_reads_key_from_env():
    conn = SmtpMailConnector.from_env(
        enabled=True,
        env={"SMTP_SENDER": "env@x.example", "SMTP_APP_PASSWORD": "env-pass"},
        smtp_factory=_FakeSmtpFactory(_FakeSmtpClient()),
    )
    assert "env-pass" not in repr(conn)
    res = conn.send("c@x.example", "s", "b")
    assert res.to == "c@x.example"


# ── publish path: fallback selection under the full gate stack ───────────────────


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


class _FakeGmail:
    def __init__(self, *, result=None, exc=None):
        self.calls: list[dict] = []
        self._result, self._exc = result, exc

    def send(self, to, subject, body, *, from_addr=None, attachments=None):
        self.calls.append({"to": to, "subject": subject, "attachments": attachments})
        if self._exc:
            raise self._exc
        return self._result


class _FakeSmtpConnector:
    def __init__(self, *, result=None, exc=None):
        self.calls: list[dict] = []
        self._result, self._exc = result, exc

    def send(self, to, subject, body, *, from_addr=None, attachments=None):
        self.calls.append({"to": to, "subject": subject, "body": body, "attachments": attachments})
        if self._exc:
            raise self._exc
        return self._result or SmtpSendResult(message_id="<mid@x>", to=to, subject=subject)


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


def _pending(**kw) -> ActionRow:
    kw.setdefault("id", "act_smtp1")
    kw.setdefault("tenant_id", _TENANT)
    kw.setdefault("type", "outreach")
    kw.setdefault("draft", "Hello from the studio")
    kw.setdefault("status", "pending")
    kw.setdefault("target", "client@studio.example")
    kw.setdefault("subject", "Your custom piece")
    kw.setdefault("idempotency_key", "k-smtp")
    return ActionRow(channel="gmail", **kw)


_INVALID_GRANT = GmailAuthError(
    "gmail token exchange failed: HTTP 400 invalid_grant: Token has been expired or revoked."
)


def test_invalid_grant_falls_back_to_smtp_and_audits(patched_store, monkeypatch, audit_rows):
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")
    patched_store(_pending())
    gmail = _FakeGmail(exc=_INVALID_GRANT)
    smtp = _FakeSmtpConnector()
    out = approve_and_publish("act_smtp1", connectors={"gmail": gmail, "smtp": smtp})

    assert out.status == "sent"
    assert out.outcome_label == "Sent (SMTP fallback)"
    assert out.transport == "gmail-smtp-fallback"
    assert out.mode == "test_redirect"  # gate stack unchanged: still the safe redirect
    # SMTP received the GATED values (redirected recipient + [TEST->...] subject).
    (call,) = smtp.calls
    assert call["to"] == "ops@inbox.example"
    assert call["subject"] == "[TEST->client@studio.example] Your custom piece"
    # audit: one 'sent' row, transport recorded as the fallback, provider id = Message-ID
    sent = [a for a in audit_rows if a["result"] == "sent"]
    assert len(sent) == 1
    assert sent[0]["transport"] == "gmail-smtp-fallback"
    assert sent[0]["provider_id"] == "<mid@x>"


def test_missing_gmail_creds_also_fall_back(patched_store, monkeypatch):
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")
    patched_store(_pending())
    gmail = _FakeGmail(exc=GmailAuthError(
        "gmail connector missing client_id/client_secret/refresh_token (key-from-env required)"
    ))
    smtp = _FakeSmtpConnector()
    out = approve_and_publish("act_smtp1", connectors={"gmail": gmail, "smtp": smtp})
    assert out.status == "sent"
    assert len(smtp.calls) == 1


def test_non_auth_gmail_error_never_falls_back(patched_store, monkeypatch):
    # A provider SEND rejection (message-level) must NOT be retried on a second
    # transport — that could double-deliver or mask a policy refusal.
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")
    patched_store(_pending())
    gmail = _FakeGmail(exc=GmailSendError("gmail send failed: HTTP 403 insufficient scope"))
    smtp = _FakeSmtpConnector()
    out = approve_and_publish("act_smtp1", connectors={"gmail": gmail, "smtp": smtp})

    assert out.status == "failed"
    assert smtp.calls == []
    assert "403" in out.last_error


def test_no_smtp_configured_keeps_the_concrete_gmail_failure(patched_store, monkeypatch):
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")
    patched_store(_pending())
    gmail = _FakeGmail(exc=_INVALID_GRANT)
    out = approve_and_publish("act_smtp1", connectors={"gmail": gmail})  # no smtp anywhere

    assert out.status == "failed"
    assert "invalid_grant" in out.last_error  # the original, concrete error — never silent


def test_smtp_env_builds_the_fallback_when_not_injected(patched_store, monkeypatch):
    # Env-configured fallback: SMTP_SENDER/SMTP_APP_PASSWORD present -> from_env
    # connector built. We monkeypatch the class's send seam to avoid any network.
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")
    monkeypatch.setenv("SMTP_SENDER", "studio@x.example")
    monkeypatch.setenv("SMTP_APP_PASSWORD", "pass")
    patched_store(_pending())
    sent_calls: list[dict] = []

    def _fake_send(self, to, subject, body, *, from_addr=None, attachments=None):
        sent_calls.append({"to": to, "subject": subject})
        return SmtpSendResult(message_id="<env@x>", to=to, subject=subject)

    monkeypatch.setattr(SmtpMailConnector, "send", _fake_send)
    gmail = _FakeGmail(exc=_INVALID_GRANT)
    out = approve_and_publish("act_smtp1", connectors={"gmail": gmail})

    assert out.status == "sent"
    assert out.transport == "gmail-smtp-fallback"
    assert sent_calls[0]["to"] == "ops@inbox.example"


def test_fallback_failure_surfaces_both_real_errors(patched_store, monkeypatch):
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")
    patched_store(_pending())
    gmail = _FakeGmail(exc=_INVALID_GRANT)
    smtp = _FakeSmtpConnector(exc=SmtpSendError("smtp send failed: (535, 'auth rejected')"))
    out = approve_and_publish("act_smtp1", connectors={"gmail": gmail, "smtp": smtp})

    assert out.status == "failed"
    assert "invalid_grant" in out.last_error and "535" in out.last_error


def test_fallback_carries_the_promised_attachment(patched_store, monkeypatch):
    from sideeffects.artifact_media import ArtifactMedia
    import sideeffects.artifact_media as am

    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")
    patched_store(_pending(context=json.dumps({"attachment_artifact_id": "art_5"})))
    monkeypatch.setattr(
        am, "load_artifact_media",
        lambda artifact_id, *, dsn=None, fetch_row=None: ArtifactMedia(
            artifact_id="art_5", filename="flash.png", mime_type="image/png",
            content_bytes=_PNG, source="storage_path",
        ),
    )
    gmail = _FakeGmail(exc=_INVALID_GRANT)
    smtp = _FakeSmtpConnector()
    out = approve_and_publish("act_smtp1", connectors={"gmail": gmail, "smtp": smtp})

    assert out.status == "sent"
    assert smtp.calls[0]["attachments"][0]["filename"] == "flash.png"
    assert out.attachment_receipts[0].filename == "flash.png"


def test_fallback_still_respects_placeholder_and_failclosed_gates(patched_store, monkeypatch):
    # The fallback lives BELOW the gate stack: an unresolved placeholder (or a
    # missing redirect with no live auth) refuses BEFORE either transport runs.
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")
    patched_store(_pending(draft="Hi {{unsubscribe}}"))
    gmail = _FakeGmail(exc=_INVALID_GRANT)
    smtp = _FakeSmtpConnector()
    out = approve_and_publish("act_smtp1", connectors={"gmail": gmail, "smtp": smtp})
    assert out.status == "failed"
    assert gmail.calls == [] and smtp.calls == []
    assert "placeholder" in out.last_error

    monkeypatch.delenv("GMAIL_REDIRECT_TO", raising=False)
    patched_store(_pending(id="act_smtp2"))
    out2 = approve_and_publish("act_smtp2", connectors={"gmail": gmail, "smtp": smtp})
    assert out2.status == "failed"
    assert out2.mode == "blocked"
    assert smtp.calls == []  # fail-closed refusal happens before ANY transport
