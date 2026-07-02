"""SMS-1 SmsConnector send path (CustomerAcq-t90.1) — PG integration.

The HARD wiring requirement from t90.3: claim_send_slot (atomic) runs BEFORE
any Twilio dispatch — Twilio has no provider-side idempotency, so the slot
claim is the only exactly-once guarantee. Includes the concurrent double-send
test at the connector boundary, the redirect sandbox proof (no real recipient
reachable while SMS_REDIRECT_TO is set), the send-time compliance-gate check,
and the webhook feeds (Advanced Opt-Out -> suppression ledger; status
callbacks -> delivery_events + carrier errors).
"""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from compliance.sms_gate import BlockCode
from connectors.sms import (
    SmsSendStatus,
    TwilioSmsConnector,
    ingest_opt_out_webhook,
    ingest_status_callback,
    send_sms_gated,
)
from suppression.ledger import (
    consent_status,
    ensure_schema,
    is_suppressed,
    record_consent,
    recipient_view,
)
from tests.test_sms_connector import _FakeFetcher, _resolver, _sign

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

DSN = os.environ.get("ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers")
UTC = timezone.utc
NOW = datetime(2026, 7, 2, 19, 0, tzinfo=UTC)  # midday in every SDT timezone
REDIRECT = "+15550001111"

BODY = "SDT: July flash sale this week - book your session. Reply STOP to opt out."


@pytest.fixture(scope="module", autouse=True)
def _schema():
    ensure_schema(DSN)


@pytest.fixture(autouse=True)
def _sandbox(monkeypatch):
    monkeypatch.setenv("SMS_REDIRECT_TO", REDIRECT)


def _tenant() -> str:
    return f"t901-{uuid.uuid4().hex[:10]}"


def _phone() -> str:
    return f"+1702555{uuid.uuid4().int % 10_000:04d}"


def _conn(**kw):
    kw.setdefault("account_sid", "ACxxxxxxxx")
    kw.setdefault("auth_token", "secret-token")
    kw.setdefault("messaging_service_sid", "MGxxxxxxxx")
    kw.setdefault("fetcher", _FakeFetcher())
    kw.setdefault("resolver", _resolver())
    return TwilioSmsConnector(enabled=True, **kw)


def _form(call) -> dict[str, str]:
    from urllib.parse import parse_qsl

    return dict(parse_qsl(call["body"].decode()))


# ── claim BEFORE dispatch (hard requirement) ─────────────────────────────────


def test_send_claims_slot_then_dispatches_once():
    tenant, phone = _tenant(), _phone()
    fake = _FakeFetcher()
    c = _conn(fetcher=fake)
    result = c.send_sms(tenant_id=tenant, to=phone, body=BODY, now=NOW, dsn=DSN)
    assert result.status is SmsSendStatus.SENT
    assert result.mode == "test_redirect"
    assert len(fake.calls) == 1
    # The slot was claimed against the REAL recipient — the sandbox send
    # consumed the frequency window (t90.3 TEST-MODE semantics).
    view = recipient_view(tenant_id=tenant, identifier=phone, channel="sms", now=NOW, dsn=DSN)
    assert len(view.recent_promo_sends) == 1


def test_second_send_in_window_refused_before_http():
    tenant, phone = _tenant(), _phone()
    fake = _FakeFetcher()
    c = _conn(fetcher=fake)
    first = c.send_sms(tenant_id=tenant, to=phone, body=BODY, now=NOW, dsn=DSN)
    assert first.status is SmsSendStatus.SENT
    second = c.send_sms(
        tenant_id=tenant, to=phone, body=BODY + " v2",
        now=NOW + timedelta(minutes=5), dsn=DSN,
    )
    assert second.status is SmsSendStatus.BLOCKED
    assert "frequency" in second.reason.lower()
    assert len(fake.calls) == 1  # no second HTTP dispatch


def test_concurrent_double_send_exactly_one_dispatch():
    # THE bead's hard AC: two racing senders, same recipient, different bodies —
    # the atomic slot claim admits exactly one Twilio dispatch.
    tenant, phone = _tenant(), _phone()
    fake = _FakeFetcher()
    c = _conn(fetcher=fake)

    def _send(i: int):
        return c.send_sms(
            tenant_id=tenant, to=phone, body=f"{BODY} variant {i}", now=NOW, dsn=DSN,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_send, range(8)))
    sent = [r for r in results if r.status is SmsSendStatus.SENT]
    assert len(sent) == 1
    assert len(fake.calls) == 1
    view = recipient_view(tenant_id=tenant, identifier=phone, channel="sms", now=NOW, dsn=DSN)
    assert len(view.recent_promo_sends) == 1


def test_suppressed_recipient_never_dispatches():
    tenant, phone = _tenant(), _phone()
    from suppression.ledger import record_suppression

    record_suppression(
        tenant_id=tenant, identifier=phone, channel="sms", reason="stop",
        raw_utterance="STOP", occurred_at=NOW - timedelta(days=1), dsn=DSN,
    )
    fake = _FakeFetcher()
    c = _conn(fetcher=fake)
    result = c.send_sms(tenant_id=tenant, to=phone, body=BODY, now=NOW, dsn=DSN)
    assert result.status is SmsSendStatus.BLOCKED
    assert "suppress" in result.reason.lower()
    assert fake.calls == []


# ── redirect sandbox: NO real recipient reachable while set (AC 3) ───────────


def test_redirect_boundary_no_real_recipient_ever_dispatched():
    tenant = _tenant()
    phones = [_phone() for _ in range(3)]
    fake = _FakeFetcher()
    c = _conn(fetcher=fake)
    for i, phone in enumerate(phones):
        r = c.send_sms(tenant_id=tenant, to=phone, body=f"{BODY} #{i}", now=NOW, dsn=DSN)
        assert r.status is SmsSendStatus.SENT
        assert r.dispatched_to == REDIRECT
    dispatched_tos = [_form(call)["To"] for call in fake.calls]
    assert dispatched_tos == [REDIRECT] * 3
    assert not set(dispatched_tos) & set(phones)  # asserted at the connector boundary


def test_redirect_body_carries_test_marker_with_real_target():
    tenant, phone = _tenant(), _phone()
    fake = _FakeFetcher()
    c = _conn(fetcher=fake)
    c.send_sms(tenant_id=tenant, to=phone, body=BODY, now=NOW, dsn=DSN)
    form = _form(fake.calls[0])
    assert form["Body"].startswith(f"[TEST->{phone}]")
    assert form["MessagingServiceSid"] == "MGxxxxxxxx"


def test_dispatch_uses_messaging_service_and_basic_auth_header():
    tenant, phone = _tenant(), _phone()
    fake = _FakeFetcher()
    c = _conn(fetcher=fake)
    c.send_sms(tenant_id=tenant, to=phone, body=BODY, now=NOW, dsn=DSN)
    call = fake.calls[0]
    assert call["host"] == "api.twilio.com"
    assert "Messages.json" in call["path"]
    assert call["headers"]["Authorization"].startswith("Basic ")
    assert "secret-token" not in call["path"]  # token never in a URL


# ── send-time compliance gate (send_sms_gated composition) ───────────────────


def test_gated_send_blocks_unconsented_recipient_zero_http():
    tenant, phone = _tenant(), _phone()
    fake = _FakeFetcher()
    c = _conn(fetcher=fake)
    gate, send = send_sms_gated(
        c, tenant_id=tenant, to=phone, body=BODY, registered_samples=(BODY,),
        trust_tier="low", daily_quota_used=0, now=NOW, dsn=DSN,
    )
    assert not gate.allowed
    assert BlockCode.NO_CONSENT in {b.code for b in gate.blocks}
    assert send is None
    assert fake.calls == []


def test_gated_send_dispatches_for_clean_consented_recipient():
    tenant, phone = _tenant(), _phone()
    record_consent(
        tenant_id=tenant, identifier=phone, channel="sms", source="web_form",
        granted_at=NOW - timedelta(days=30), dsn=DSN,
    )
    fake = _FakeFetcher()
    c = _conn(fetcher=fake)
    gate, send = send_sms_gated(
        c, tenant_id=tenant, to=phone, body=BODY, registered_samples=(BODY,),
        trust_tier="low", daily_quota_used=0, now=NOW, dsn=DSN,
    )
    assert gate.allowed, gate.blocks
    assert send is not None and send.status is SmsSendStatus.SENT
    assert len(fake.calls) == 1
    assert _form(fake.calls[0])["To"] == REDIRECT  # sandbox still redirects


# ── Advanced Opt-Out webhook -> suppression ledger ───────────────────────────


def test_opt_out_webhook_verifies_signature_and_flips_consent_and_suppression():
    tenant, phone = _tenant(), _phone()
    record_consent(
        tenant_id=tenant, identifier=phone, channel="sms", source="web_form",
        granted_at=NOW - timedelta(days=30), dsn=DSN,
    )
    token, url = "secret-token", "https://hooks.example.com/sms/optout"
    params = {"From": phone, "OptOutType": "STOP", "Body": "STOP"}
    row_id = ingest_opt_out_webhook(
        params, tenant_id=tenant, url=url, signature=_sign(token, url, params),
        auth_token=token, occurred_at=NOW, dsn=DSN,
    )
    assert row_id is not None
    assert is_suppressed(tenant_id=tenant, identifier=phone, channel="sms", dsn=DSN)
    status = consent_status(tenant_id=tenant, identifier=phone, channel="sms", dsn=DSN)
    assert status is not None and not status.active


def test_opt_out_webhook_rejects_bad_signature_writes_nothing():
    tenant, phone = _tenant(), _phone()
    token, url = "secret-token", "https://hooks.example.com/sms/optout"
    params = {"From": phone, "OptOutType": "STOP", "Body": "STOP"}
    from connectors.sms import SmsWebhookSignatureError

    with pytest.raises(SmsWebhookSignatureError):
        ingest_opt_out_webhook(
            params, tenant_id=tenant, url=url, signature="bogus",
            auth_token=token, occurred_at=NOW, dsn=DSN,
        )
    assert not is_suppressed(tenant_id=tenant, identifier=phone, channel="sms", dsn=DSN)


# ── status callbacks -> delivery_events (+ carrier errors) ───────────────────


def test_status_callback_ingested_and_retry_deduped():
    tenant, phone = _tenant(), _phone()
    sid = f"SM{uuid.uuid4().hex}"
    form = {"MessageSid": sid, "MessageStatus": "delivered", "To": phone}
    for _ in range(2):  # webhook retry
        ingest_status_callback(form, tenant_id=tenant, occurred_at=NOW, dsn=DSN)
    with psycopg.connect(DSN, autocommit=True) as conn:
        n = conn.execute(
            "SELECT count(*) FROM delivery_events WHERE provider_sid=%s AND status='delivered'",
            (sid,),
        ).fetchone()[0]
    assert n == 1


def test_status_callback_carrier_error_auto_suppresses():
    tenant, phone = _tenant(), _phone()
    sid = f"SM{uuid.uuid4().hex}"
    form = {
        "MessageSid": sid, "MessageStatus": "undelivered", "To": phone,
        "ErrorCode": "30004",
    }
    ingest_status_callback(form, tenant_id=tenant, occurred_at=NOW, dsn=DSN)
    assert is_suppressed(tenant_id=tenant, identifier=phone, channel="sms", dsn=DSN)
    with psycopg.connect(DSN, autocommit=True) as conn:
        n = conn.execute(
            "SELECT count(*) FROM delivery_events WHERE provider_sid=%s", (sid,),
        ).fetchone()[0]
    assert n == 1


def test_delivery_rows_written_under_redirect():
    # AC 5+8: the callback machinery works under the sandbox redirect — a
    # test_redirect send's delivery events land like a real one's.
    tenant, phone = _tenant(), _phone()
    fake = _FakeFetcher()
    c = _conn(fetcher=fake)
    result = c.send_sms(tenant_id=tenant, to=phone, body=BODY, now=NOW, dsn=DSN)
    assert result.status is SmsSendStatus.SENT
    sid = result.provider.provider_id
    ingest_status_callback(
        {"MessageSid": sid, "MessageStatus": "delivered", "To": REDIRECT},
        tenant_id=tenant, occurred_at=NOW, dsn=DSN,
    )
    with psycopg.connect(DSN, autocommit=True) as conn:
        n = conn.execute(
            "SELECT count(*) FROM delivery_events WHERE provider_sid=%s", (sid,),
        ).fetchone()[0]
    assert n == 1
