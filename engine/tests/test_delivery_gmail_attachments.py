"""DELIVERY item 1 — Gmail attachments (spec §10/§13): fail-closed, audited, no live send.

Covers the three layers:

* :mod:`connectors.mail_message` — validation (allowed types png/jpeg/webp/pdf,
  20MB total cap, concrete errors) + receipts (filename/sha256/size, never bytes);
* :class:`connectors.gmail.GmailConnector` — attachments ride the RFC822 ``raw``
  as multipart, an invalid attachment refuses BEFORE any network call, the plain
  no-attachment message is unchanged (back-compat);
* :func:`actions.publish.approve_and_publish` — the approve path reads the
  action's ``context`` JSON, loads the promised artifact's bytes, attaches them,
  and FAILS CLOSED (no send) when the promised artifact cannot be loaded —
  a promised attachment is never silently dropped. Missing context fields no-op.

All through fakes (fake Gmail transport / fake store / fake artifact rows) — no
live send, no secret values.
"""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

import actions.publish as publish
from actions.publish import approve_and_publish
from actions.store import ActionRow
from connectors.gmail import GmailConnector, GmailSendResult, HttpResult
from connectors.mail_message import (
    ALLOWED_ATTACHMENT_MIME_TYPES,
    MAX_ATTACHMENT_TOTAL_BYTES,
    MailAttachmentError,
    build_mail_message,
    validate_attachments,
)
from sideeffects.artifact_media import ArtifactMedia, ArtifactMediaError, load_artifact_media

_PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-bytes"
_TENANT = "test_delivery_tenant"


@pytest.fixture(autouse=True)
def _tenant_passthrough(monkeypatch):
    # The server-side TEST-MODE gate refuses unknown tenants unless explicitly
    # allowlisted (wwy.4). These tests use a dedicated tenant with no registry row.
    monkeypatch.setenv("TEST_MODE_LEGACY_PASSTHROUGH", _TENANT)


def _att(filename="art.png", content=_PNG, mime="image/png") -> dict:
    return {"filename": filename, "content_bytes": content, "mime_type": mime}


# ── mail_message: validation + receipts ─────────────────────────────────────────


def test_validate_attachments_none_is_clean_noop():
    assert validate_attachments(None) == ([], ())
    assert validate_attachments([]) == ([], ())


def test_validate_attachments_receipts_carry_sha256_never_bytes():
    normalized, receipts = validate_attachments([_att()])
    assert normalized[0]["mime_type"] == "image/png"
    (r,) = receipts
    assert r.filename == "art.png"
    assert r.size_bytes == len(_PNG)
    assert r.sha256 == hashlib.sha256(_PNG).hexdigest()
    assert r.sha256_prefix == r.sha256[:12]
    assert r.sha256_prefix in r.audit_label() and "art.png" in r.audit_label()
    # the receipt is audit-safe: no content bytes anywhere on it
    assert not hasattr(r, "content_bytes")


def test_validate_attachments_normalizes_jpg_alias():
    normalized, _ = validate_attachments([_att(mime="image/JPG")])
    assert normalized[0]["mime_type"] == "image/jpeg"


@pytest.mark.parametrize("mime", ["image/gif", "text/html", "application/zip", "application/x-msdownload"])
def test_validate_attachments_rejects_disallowed_types_concretely(mime):
    with pytest.raises(MailAttachmentError) as ei:
        validate_attachments([_att(mime=mime)])
    assert mime.lower() in str(ei.value)
    assert "not allowed" in str(ei.value)
    assert mime not in ALLOWED_ATTACHMENT_MIME_TYPES


def test_validate_attachments_rejects_over_20mb_total():
    half = b"x" * (MAX_ATTACHMENT_TOTAL_BYTES // 2 + 1)
    with pytest.raises(MailAttachmentError) as ei:
        validate_attachments([_att("a.png", half), _att("b.png", half)])
    assert "total cap" in str(ei.value)
    assert "b.png" in str(ei.value)  # names the attachment that crossed the cap


def test_validate_attachments_rejects_missing_fields():
    with pytest.raises(MailAttachmentError, match="filename"):
        validate_attachments([{"content_bytes": _PNG, "mime_type": "image/png"}])
    with pytest.raises(MailAttachmentError, match="content_bytes"):
        validate_attachments([_att(content=b"")])
    with pytest.raises(MailAttachmentError, match="mime_type"):
        validate_attachments([{"filename": "a.png", "content_bytes": _PNG}])
    with pytest.raises(MailAttachmentError, match="mapping"):
        validate_attachments(["not-a-dict"])  # type: ignore[list-item]


def test_build_mail_message_without_attachments_stays_single_part():
    msg, receipts = build_mail_message(to="a@x.example", subject="s", body="b")
    assert receipts == ()
    assert not msg.is_multipart()


def test_build_mail_message_with_attachment_is_multipart_with_part():
    msg, receipts = build_mail_message(
        to="a@x.example", subject="s", body="hello", attachments=[_att()]
    )
    assert msg.is_multipart()
    parts = list(msg.iter_attachments())
    assert len(parts) == 1
    assert parts[0].get_filename() == "art.png"
    assert parts[0].get_content_type() == "image/png"
    assert parts[0].get_content() == _PNG
    assert receipts[0].mime_type == "image/png"


# ── gmail connector: attachments in the raw message, fail-closed pre-network ────


class _FakeTransport:
    def __init__(self, *responses: HttpResult) -> None:
        self.calls: list[dict] = []
        self._responses = list(responses)

    def __call__(self, *, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "body": body})
        return self._responses[len(self.calls) - 1]


_TOKEN_OK = HttpResult(200, b'{"access_token": "ya29.FAKE", "expires_in": 3599}')
_SEND_OK = HttpResult(200, b'{"id": "msg123", "threadId": "th123"}')


def _conn(transport):
    return GmailConnector(
        client_id="cid", client_secret="cs", refresh_token="rt",
        transport=transport, enabled=True,
    )


def test_gmail_send_with_attachment_rides_raw_multipart_and_receipts():
    fake = _FakeTransport(_TOKEN_OK, _SEND_OK)
    res = _conn(fake).send(
        "c@x.example", "Your piece", "see attached", attachments=[_att()]
    )
    raw = base64.urlsafe_b64decode(json.loads(fake.calls[1]["body"])["raw"].encode())
    rfc822 = raw.decode("utf-8", "replace")
    assert "multipart/mixed" in rfc822
    assert 'filename="art.png"' in rfc822
    assert base64.b64encode(_PNG).decode()[:24] in rfc822.replace("\n", "")
    assert isinstance(res, GmailSendResult)
    assert res.message_id == "msg123"
    assert [r.filename for r in res.attachments] == ["art.png"]
    assert res.attachments[0].sha256 == hashlib.sha256(_PNG).hexdigest()


def test_gmail_send_without_attachments_is_unchanged_backcompat():
    fake = _FakeTransport(_TOKEN_OK, _SEND_OK)
    res = _conn(fake).send("c@x.example", "s", "b")
    raw = base64.urlsafe_b64decode(json.loads(fake.calls[1]["body"])["raw"].encode())
    assert b"multipart" not in raw
    assert res.attachments == ()


def test_gmail_invalid_attachment_refuses_before_any_network_call():
    fake = _FakeTransport(_TOKEN_OK, _SEND_OK)
    with pytest.raises(MailAttachmentError):
        _conn(fake).send("c@x.example", "s", "b", attachments=[_att(mime="image/gif")])
    assert fake.calls == []  # not even the token exchange happened — fail closed


# ── artifact_media loader: storage_path / base64 / preview, concrete errors ─────


def _row(**kw) -> dict:
    base = {
        "id": "art_1", "tenant_id": _TENANT, "name": "sleeve.png",
        "artifact_type": "artwork", "media_type": "image/png",
        "meta": {}, "active": True, "preview": None,
    }
    base.update(kw)
    return base


def test_load_artifact_media_reads_storage_path_from_disk(tmp_path):
    p = tmp_path / "sleeve.png"
    p.write_bytes(_PNG)
    media = load_artifact_media(
        "art_1", fetch_row=lambda aid, dsn=None: _row(storage_path=str(p))
    )
    assert media.content_bytes == _PNG
    assert media.source == "storage_path"
    assert media.mime_type == "image/png"
    assert media.filename == "sleeve.png"


def test_load_artifact_media_storage_path_in_meta_and_relative_to_cwd(tmp_path, monkeypatch):
    (tmp_path / "var" / "artifacts").mkdir(parents=True)
    (tmp_path / "var" / "artifacts" / "a.png").write_bytes(_PNG)
    monkeypatch.chdir(tmp_path)
    media = load_artifact_media(
        "art_1",
        fetch_row=lambda aid, dsn=None: _row(meta={"storage_path": "var/artifacts/a.png"}),
    )
    assert media.content_bytes == _PNG
    assert media.source == "storage_path"


def test_load_artifact_media_missing_file_is_concrete_error():
    with pytest.raises(ArtifactMediaError, match="does not exist on disk"):
        load_artifact_media(
            "art_1", fetch_row=lambda aid, dsn=None: _row(storage_path="/nope/gone.png")
        )


def test_load_artifact_media_falls_back_to_base64_column():
    media = load_artifact_media(
        "art_1",
        fetch_row=lambda aid, dsn=None: _row(
            content_b64=base64.b64encode(_PNG).decode()
        ),
    )
    assert media.content_bytes == _PNG
    assert media.source == "base64_column"


def test_load_artifact_media_falls_back_to_preview_data_uri():
    uri = "data:image/webp;base64," + base64.b64encode(_PNG).decode()
    media = load_artifact_media("art_1", fetch_row=lambda aid, dsn=None: _row(preview=uri))
    assert media.content_bytes == _PNG
    assert media.source == "preview_data_uri"
    assert media.mime_type == "image/webp"  # the data-uri's own mime wins


def test_load_artifact_media_missing_row_removed_row_and_no_content():
    with pytest.raises(ArtifactMediaError, match="not found"):
        load_artifact_media("art_x", fetch_row=lambda aid, dsn=None: None)
    with pytest.raises(ArtifactMediaError, match="removed"):
        load_artifact_media("art_1", fetch_row=lambda aid, dsn=None: _row(active=False))
    with pytest.raises(ArtifactMediaError, match="not recoverable"):
        load_artifact_media("art_1", fetch_row=lambda aid, dsn=None: _row())


def test_load_artifact_media_store_error_fails_closed():
    def _boom(aid, dsn=None):
        raise RuntimeError("db down")

    with pytest.raises(ArtifactMediaError, match="db down"):
        load_artifact_media("art_1", fetch_row=_boom)


# ── publish approve path: promised attachment attached / fail-closed / no-op ────


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
    """Attachment-aware fake: records the attachments kwarg it was sent."""

    def __init__(self, *, result=None, exc=None):
        self.calls: list[dict] = []
        self._result, self._exc = result, exc

    def send(self, to, subject, body, *, from_addr=None, attachments=None):
        self.calls.append({"to": to, "subject": subject, "body": body, "attachments": attachments})
        if self._exc:
            raise self._exc
        return self._result


class _LegacyFakeGmail:
    """A connector WITHOUT attachment support (pre-attachment signature)."""

    def __init__(self):
        self.calls: list[tuple] = []

    def send(self, to, subject, body, *, from_addr=None):
        self.calls.append((to, subject, body))
        return GmailSendResult(message_id="m1", deep_link="dl")


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

    def _capture(action, **kw):
        rows.append({"action_id": action.id, **kw})

    monkeypatch.setattr(publish, "_record_send_audit_row", _capture)
    return rows


def _pending(**kw) -> ActionRow:
    kw.setdefault("id", "act_del1")
    kw.setdefault("tenant_id", _TENANT)
    kw.setdefault("type", "outreach")
    kw.setdefault("draft", "Hello — flash sheet attached")
    kw.setdefault("status", "pending")
    kw.setdefault("target", "client@studio.example")
    kw.setdefault("subject", "Your custom piece")
    kw.setdefault("idempotency_key", "k1")
    return ActionRow(channel="gmail", **kw)


def _patch_loader(monkeypatch, media=None, exc=None):
    import sideeffects.artifact_media as am

    def _load(artifact_id, *, dsn=None, fetch_row=None):
        if exc is not None:
            raise exc
        return media

    monkeypatch.setattr(am, "load_artifact_media", _load)


def test_approve_attaches_promised_artifact_and_audits(patched_store, monkeypatch, audit_rows):
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")
    ctx = json.dumps({"attachment_artifact_id": "art_9", "artwork": {"assetId": "as1"}})
    patched_store(_pending(context=ctx))
    _patch_loader(
        monkeypatch,
        media=ArtifactMedia(
            artifact_id="art_9", filename="sleeve.png", mime_type="image/png",
            content_bytes=_PNG, source="storage_path",
        ),
    )
    gmail = _FakeGmail(result=GmailSendResult(message_id="m1", deep_link="dl"))
    out = approve_and_publish("act_del1", connectors={"gmail": gmail})

    assert out.status == "sent"
    (call,) = gmail.calls
    assert call["attachments"] == [
        {"filename": "sleeve.png", "content_bytes": _PNG, "mime_type": "image/png"}
    ]
    # the returned row carries the audit receipts (filename + sha256 prefix)
    (r,) = out.attachment_receipts
    assert r.filename == "sleeve.png"
    assert r.sha256_prefix == hashlib.sha256(_PNG).hexdigest()[:12]
    # ONE consistent send-audit row: transport + attachment label + provider id
    sent_rows = [a for a in audit_rows if a["result"] == "sent"]
    assert len(sent_rows) == 1
    assert sent_rows[0]["transport"] == "gmail-api"
    assert sent_rows[0]["provider_id"] == "m1"
    labels = [x.audit_label() for x in sent_rows[0]["attachments"]]
    assert any("sleeve.png" in lab and r.sha256_prefix in lab for lab in labels)


def test_promised_artifact_unloadable_fails_closed_no_send(patched_store, monkeypatch):
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")
    ctx = json.dumps({"attachment_artifact_id": "art_gone"})
    patched_store(_pending(context=ctx))
    _patch_loader(monkeypatch, exc=ArtifactMediaError("artifact 'art_gone' not found in context_artifacts"))
    gmail = _FakeGmail(result=GmailSendResult(message_id="m1", deep_link="dl"))
    out = approve_and_publish("act_del1", connectors={"gmail": gmail})

    assert out.status == "failed"
    assert gmail.calls == []  # NEVER sent without the promised attachment
    assert "art_gone" in out.last_error
    assert "refusing to send without it" in out.last_error


def test_promised_artifact_with_disallowed_type_fails_closed(patched_store, monkeypatch):
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")
    patched_store(_pending(context=json.dumps({"attachment_artifact_id": "art_bad"})))
    _patch_loader(
        monkeypatch,
        media=ArtifactMedia(
            artifact_id="art_bad", filename="malware.exe", mime_type="application/x-msdownload",
            content_bytes=b"MZ...", source="storage_path",
        ),
    )
    gmail = _FakeGmail(result=GmailSendResult(message_id="m1", deep_link="dl"))
    out = approve_and_publish("act_del1", connectors={"gmail": gmail})

    assert out.status == "failed"
    assert gmail.calls == []
    assert "not allowed" in out.last_error


def test_artwork_artifact_id_also_counts_as_promise(patched_store, monkeypatch):
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")
    ctx = json.dumps({"artwork": {"assetId": "as1", "artifactId": "art_7", "vlmSummary": "a rose"}})
    patched_store(_pending(context=ctx))
    _patch_loader(
        monkeypatch,
        media=ArtifactMedia(
            artifact_id="art_7", filename="rose.webp", mime_type="image/webp",
            content_bytes=_PNG, source="preview_data_uri",
        ),
    )
    gmail = _FakeGmail(result=GmailSendResult(message_id="m2", deep_link="dl"))
    out = approve_and_publish("act_del1", connectors={"gmail": gmail})
    assert out.status == "sent"
    assert gmail.calls[0]["attachments"][0]["filename"] == "rose.webp"


def test_no_context_is_graceful_noop_with_legacy_connector(patched_store, monkeypatch):
    # No context / no artwork fields: nothing is promised, nothing is attached, and a
    # legacy connector without the attachments kwarg still works unchanged.
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")
    patched_store(_pending(context=None))
    gmail = _LegacyFakeGmail()
    out = approve_and_publish("act_del1", connectors={"gmail": gmail})
    assert out.status == "sent"
    assert len(gmail.calls) == 1


def test_malformed_context_json_is_graceful_noop(patched_store, monkeypatch):
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "ops@inbox.example")
    patched_store(_pending(context="{not json"))
    gmail = _FakeGmail(result=GmailSendResult(message_id="m1", deep_link="dl"))
    out = approve_and_publish("act_del1", connectors={"gmail": gmail})
    assert out.status == "sent"
    assert gmail.calls[0]["attachments"] is None

# Whole module needs a live Postgres (ENGINE_DATABASE_URL): it runs in the CI
# integration lane (schema applied via initdb + bootstrap), not the DB-free unit lane.
pytestmark = pytest.mark.integration
