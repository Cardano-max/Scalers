"""SMS-3 cross-channel STOP/suppression ledger (CustomerAcq-t90.3, P1) — PG integration.

The ledger is the ONE source of suppression truth: sms_gate, send-time
eligibility, audience creation, and the email path all read it. Covers the
bead's named repros and the arch addendum (AC #10): a STOP writes BOTH the
suppression row AND the consent revocation (plus the bi-temporal
contact-preference supersede) in one transaction, so the recipient is blocked
independently by the consent check AND the suppression check.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from compliance.sms_gate import BlockCode, MessageContext, SendContext, evaluate_sms
from outreach.schema import Prospect
from outreach.suppression import SuppressionGate
from suppression.ledger import (
    carrier_30007_spike,
    claim_send_slot,
    consent_status,
    ensure_schema,
    filter_audience,
    get_memories,
    ingest_email_unsubscribe,
    ingest_manual_revocation,
    ingest_twilio_opt_out,
    is_suppressed,
    recipient_context_for_gate,
    recipient_view,
    record_carrier_error,
    record_consent,
    record_preference_memory,
    record_send_event,
    record_suppression,
    send_backstop,
)

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

DSN = os.environ.get("ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers")
UTC = timezone.utc
NOW = datetime(2026, 7, 2, 19, 0, tzinfo=UTC)  # midday in every SDT timezone

BODY = "SDT: July flash sale this week - book your session. Reply STOP to opt out."
SAMPLES = (BODY,)


@pytest.fixture(scope="module", autouse=True)
def _schema():
    ensure_schema(DSN)


def _tenant() -> str:
    return f"t903-{uuid.uuid4().hex[:10]}"


def _phone() -> str:
    return f"+1702555{uuid.uuid4().int % 10_000:04d}"


def _consent(tenant: str, phone: str, channel: str = "sms") -> int:
    return record_consent(
        tenant_id=tenant, identifier=phone, channel=channel, source="web_form",
        granted_at=NOW - timedelta(days=30), dsn=DSN,
    )


# ── schema + ledger row basics ───────────────────────────────────────────────


def test_ensure_schema_idempotent():
    ensure_schema(DSN)
    ensure_schema(DSN)


def test_record_suppression_idempotent_same_event_one_row():
    tenant, phone = _tenant(), _phone()
    a = record_suppression(
        tenant_id=tenant, identifier=phone, channel="sms", reason="stop",
        raw_utterance="STOP", occurred_at=NOW, dsn=DSN,
    )
    b = record_suppression(
        tenant_id=tenant, identifier=phone, channel="sms", reason="stop",
        raw_utterance="STOP", occurred_at=NOW, dsn=DSN,
    )
    assert a == b
    with psycopg.connect(DSN, autocommit=True) as conn:
        n = conn.execute(
            "SELECT count(*) FROM suppression_ledger WHERE tenant_id=%s AND identifier=%s",
            (tenant, phone),
        ).fetchone()[0]
    assert n == 1


def test_is_suppressed_matches_channel_or_all():
    tenant, phone = _tenant(), _phone()
    record_suppression(
        tenant_id=tenant, identifier=phone, channel="all", reason="web_form",
        raw_utterance=None, occurred_at=NOW, dsn=DSN,
    )
    assert is_suppressed(tenant_id=tenant, identifier=phone, channel="sms", dsn=DSN)
    assert is_suppressed(tenant_id=tenant, identifier=phone, channel="email", dsn=DSN)
    assert not is_suppressed(tenant_id=tenant, identifier=_phone(), channel="sms", dsn=DSN)


# ── AC #10: STOP flips BOTH consent and suppression (+ memory supersede) ─────


def test_twilio_stop_writes_suppression_and_revokes_consent_and_supersedes_memory():
    tenant, phone = _tenant(), _phone()
    _consent(tenant, phone)
    mem_id = record_preference_memory(
        tenant_id=tenant, identifier=phone,
        content={"kind": "contact_preference", "cadence": "weekly"},
        valid_from=NOW - timedelta(days=30), dsn=DSN,
    )
    stop_at = NOW - timedelta(hours=1)
    ingest_twilio_opt_out(
        {"OptOutType": "STOP", "From": phone, "Body": "STOP"},
        tenant_id=tenant, occurred_at=stop_at, dsn=DSN,
    )
    # Suppression row exists and blocks sms.
    assert is_suppressed(tenant_id=tenant, identifier=phone, channel="sms", dsn=DSN)
    # Consent is REVOKED (4.3-2): revoked_at set, no active consent.
    status = consent_status(tenant_id=tenant, identifier=phone, channel="sms", dsn=DSN)
    assert status is not None
    assert status.revoked_at == stop_at
    assert not status.active
    # Bi-temporal supersede (4.3-5): old memory row closed (valid_to + superseded_by),
    # NOT deleted; the superseding row is a do-not-contact preference.
    rows = get_memories(tenant_id=tenant, identifier=phone, dsn=DSN)
    old = next(r for r in rows if r["id"] == mem_id)
    assert old["valid_to"] == stop_at
    assert old["superseded_by"] is not None
    new = next(r for r in rows if r["id"] == old["superseded_by"])
    assert new["content"].get("do_not_contact") is True
    assert new["valid_to"] is None


def test_after_stop_gate_blocks_by_consent_check_AND_suppression_check():
    # The arch addendum's test: post-STOP, sms_gate check-1 (consent) and
    # check-2 (suppression) EACH block the recipient — defense in depth.
    tenant, phone = _tenant(), _phone()
    _consent(tenant, phone)
    ingest_twilio_opt_out(
        {"OptOutType": "STOP", "From": phone, "Body": "STOP"},
        tenant_id=tenant, occurred_at=NOW - timedelta(hours=1), dsn=DSN,
    )
    recipient = recipient_context_for_gate(
        tenant_id=tenant, phone=phone, now=NOW, dsn=DSN,
    )
    result = evaluate_sms(
        recipient,
        MessageContext(body=BODY, registered_samples=SAMPLES),
        SendContext(now=NOW, trust_tier="low", daily_quota_used=0),
    )
    got = {b.code for b in result.blocks}
    assert BlockCode.NO_CONSENT in got
    assert BlockCode.SUPPRESSED in got


def test_before_stop_gate_passes_with_ledger_fed_context():
    tenant, phone = _tenant(), _phone()
    _consent(tenant, phone)
    recipient = recipient_context_for_gate(
        tenant_id=tenant, phone=phone, now=NOW, dsn=DSN,
    )
    result = evaluate_sms(
        recipient,
        MessageContext(body=BODY, registered_samples=SAMPLES),
        SendContext(now=NOW, trust_tier="low", daily_quota_used=0),
    )
    assert result.allowed, result.blocks


# ── ingestion paths: email unsub, web/verbal, carrier errors ─────────────────


def test_email_unsubscribe_ingests_and_blocks_email():
    tenant = _tenant()
    email = f"lead-{uuid.uuid4().hex[:8]}@example.com"
    ingest_email_unsubscribe(
        tenant_id=tenant, email=email, occurred_at=NOW,
        raw_utterance="one-click unsubscribe", dsn=DSN,
    )
    assert is_suppressed(tenant_id=tenant, identifier=email, channel="email", dsn=DSN)


def test_web_form_revocation_is_cross_channel_and_feeds_email_gate():
    # FCC any-reasonable-means: a web-form revocation suppresses EVERY channel,
    # and the outreach (email) SuppressionGate loads it from the SAME ledger.
    tenant = _tenant()
    email = f"lead-{uuid.uuid4().hex[:8]}@example.com"
    ingest_manual_revocation(
        tenant_id=tenant, identifier=email, kind="web_form",
        occurred_at=NOW, raw_utterance="please stop contacting me", dsn=DSN,
    )
    assert is_suppressed(tenant_id=tenant, identifier=email, channel="sms", dsn=DSN)
    assert is_suppressed(tenant_id=tenant, identifier=email, channel="email", dsn=DSN)
    gate = SuppressionGate.from_ledger(tenant_id=tenant, dsn=DSN)
    assert gate.is_suppressed(email)
    hit = gate.check(Prospect(email=email))
    assert hit.suppressed


def test_verbal_revocation_ingestion_path_exists():
    tenant, phone = _tenant(), _phone()
    ingest_manual_revocation(
        tenant_id=tenant, identifier=phone, kind="verbal",
        occurred_at=NOW, raw_utterance="asked at front desk", dsn=DSN,
    )
    assert is_suppressed(tenant_id=tenant, identifier=phone, channel="sms", dsn=DSN)


@pytest.mark.parametrize("code", [30003, 30004, 30005, 30006])
def test_carrier_errors_auto_suppress(code):
    tenant, phone = _tenant(), _phone()
    suppressed = record_carrier_error(
        tenant_id=tenant, identifier=phone, code=code, occurred_at=NOW, dsn=DSN,
    )
    assert suppressed is True
    assert is_suppressed(tenant_id=tenant, identifier=phone, channel="sms", dsn=DSN)


def test_carrier_30007_alerts_on_spike_without_suppressing():
    tenant = _tenant()
    for _ in range(6):
        suppressed = record_carrier_error(
            tenant_id=tenant, identifier=_phone(), code=30007, occurred_at=NOW, dsn=DSN,
        )
        assert suppressed is False
    spiking, count = carrier_30007_spike(
        tenant_id=tenant, window_minutes=60, threshold=5, now=NOW, dsn=DSN,
    )
    assert spiking is True
    assert count == 6
    quiet_tenant = _tenant()
    spiking, count = carrier_30007_spike(
        tenant_id=quiet_tenant, window_minutes=60, threshold=5, now=NOW, dsn=DSN,
    )
    assert spiking is False and count == 0


# ── AC 4: audience filtered BEFORE creation, honest counts ───────────────────


def test_filter_audience_removes_suppressed_with_honest_counts():
    tenant = _tenant()
    phones = [_phone() for _ in range(5)]
    for p in phones[:2]:
        record_suppression(
            tenant_id=tenant, identifier=p, channel="sms", reason="stop",
            raw_utterance="STOP", occurred_at=NOW, dsn=DSN,
        )
    result = filter_audience(
        tenant_id=tenant, identifiers=phones, channel="sms", dsn=DSN,
    )
    assert set(result.kept) == set(phones[2:])
    assert {r[0] for r in result.removed} == set(phones[:2])
    assert len(result.kept) + len(result.removed) == len(phones)
    assert all(reason for _, reason in result.removed)


# ── send events + frequency backstop (AC 2, AC 8) ────────────────────────────


def test_ten_sends_to_one_address_repro_now_sends_once():
    # DB proof from the bead: one address received 10 sends. With the backstop,
    # 10 sequential attempts inside the 72h window deliver exactly once.
    tenant, phone = _tenant(), _phone()
    sent = 0
    for attempt in range(10):
        when = NOW + timedelta(minutes=attempt)
        ok, _reason = send_backstop(
            tenant_id=tenant, identifier=phone, channel="sms", now=when, dsn=DSN,
        )
        if ok:
            record_send_event(
                tenant_id=tenant, identifier=phone, channel="sms", kind="promo",
                mode="live", occurred_at=when, dsn=DSN,
            )
            sent += 1
    assert sent == 1


def test_send_backstop_allows_after_window_expires():
    tenant, phone = _tenant(), _phone()
    record_send_event(
        tenant_id=tenant, identifier=phone, channel="sms", kind="promo",
        mode="live", occurred_at=NOW - timedelta(hours=73), dsn=DSN,
    )
    ok, _ = send_backstop(tenant_id=tenant, identifier=phone, channel="sms", now=NOW, dsn=DSN)
    assert ok


def test_send_backstop_permanent_after_stop_even_outside_window():
    tenant, phone = _tenant(), _phone()
    record_suppression(
        tenant_id=tenant, identifier=phone, channel="sms", reason="stop",
        raw_utterance="STOP", occurred_at=NOW - timedelta(days=365), dsn=DSN,
    )
    ok, reason = send_backstop(
        tenant_id=tenant, identifier=phone, channel="sms", now=NOW, dsn=DSN,
    )
    assert ok is False
    assert "suppress" in reason.lower()


def test_send_backstop_fail_closed_when_ledger_unreachable():
    ok, reason = send_backstop(
        tenant_id="t", identifier="+17025550000", channel="sms", now=NOW,
        dsn="postgresql://scalers:scalers@localhost:59999/nope?connect_timeout=1",
    )
    assert ok is False
    assert "fail" in reason.lower() or "unavailable" in reason.lower()


def test_test_mode_redirected_send_still_writes_delivery_row():
    # AC 8: sandbox sends (SMS_REDIRECT_TO) still write ledger/delivery events —
    # the machinery is proven before go-live, and the frequency window counts them.
    tenant, phone = _tenant(), _phone()
    record_send_event(
        tenant_id=tenant, identifier=phone, channel="sms", kind="promo",
        mode="test_redirect", occurred_at=NOW - timedelta(hours=1), dsn=DSN,
    )
    view = recipient_view(
        tenant_id=tenant, identifier=phone, channel="sms", now=NOW, dsn=DSN,
    )
    assert view.suppressed is False
    assert len(view.recent_promo_sends) == 1
    ok, _ = send_backstop(tenant_id=tenant, identifier=phone, channel="sms", now=NOW, dsn=DSN)
    assert ok is False  # the redirected send consumed the window — no bypass


def test_record_send_event_idempotent_on_key():
    tenant, phone = _tenant(), _phone()
    key = f"{tenant}:sms:{phone}:abc123"
    a = record_send_event(
        tenant_id=tenant, identifier=phone, channel="sms", kind="promo",
        mode="live", idempotency_key=key, occurred_at=NOW, dsn=DSN,
    )
    b = record_send_event(
        tenant_id=tenant, identifier=phone, channel="sms", kind="promo",
        mode="live", idempotency_key=key, occurred_at=NOW, dsn=DSN,
    )
    assert a == b
    view = recipient_view(tenant_id=tenant, identifier=phone, channel="sms", now=NOW, dsn=DSN)
    assert len(view.recent_promo_sends) == 1


def test_record_send_event_in_callers_transaction_atomicity():
    # W2 wiring capability: the send path writes the delivery row in the SAME
    # transaction that settles the side-effect ledger — a rollback leaves nothing.
    tenant, phone = _tenant(), _phone()
    with psycopg.connect(DSN) as conn:
        record_send_event(
            tenant_id=tenant, identifier=phone, channel="sms", kind="promo",
            mode="live", occurred_at=NOW, conn=conn,
        )
        conn.rollback()
    view = recipient_view(tenant_id=tenant, identifier=phone, channel="sms", now=NOW, dsn=DSN)
    assert view.recent_promo_sends == ()


# ── atomic send-slot claim (adversarial-review W7: backstop was check-then-act) ─


def test_concurrent_send_claims_exactly_one_wins():
    # W7: two dispatcher workers with DIFFERENT drafts for the same phone must
    # not both pass the frequency check. claim_send_slot checks AND consumes the
    # window in one serialized transaction — 10 concurrent claims, 1 winner.
    from concurrent.futures import ThreadPoolExecutor

    tenant, phone = _tenant(), _phone()

    def _claim(i: int):
        return claim_send_slot(
            tenant_id=tenant, identifier=phone, channel="sms", mode="live",
            idempotency_key=f"{tenant}:sms:{phone}:draft{i}", now=NOW, dsn=DSN,
        )

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(_claim, range(10)))
    assert sum(1 for ok, _ in results if ok) == 1
    view = recipient_view(tenant_id=tenant, identifier=phone, channel="sms", now=NOW, dsn=DSN)
    assert len(view.recent_promo_sends) == 1


def test_claim_send_slot_blocked_when_suppressed():
    tenant, phone = _tenant(), _phone()
    record_suppression(
        tenant_id=tenant, identifier=phone, channel="sms", reason="stop",
        raw_utterance="STOP", occurred_at=NOW - timedelta(days=1), dsn=DSN,
    )
    ok, reason = claim_send_slot(
        tenant_id=tenant, identifier=phone, channel="sms", mode="live", now=NOW, dsn=DSN,
    )
    assert ok is False
    assert "suppress" in reason.lower()
    view = recipient_view(tenant_id=tenant, identifier=phone, channel="sms", now=NOW, dsn=DSN)
    assert view.recent_promo_sends == ()  # a refused claim consumes nothing


def test_claim_send_slot_fail_closed_when_unreachable():
    ok, reason = claim_send_slot(
        tenant_id="t", identifier="+17025550000", channel="sms", mode="live", now=NOW,
        dsn="postgresql://scalers:scalers@localhost:59999/nope?connect_timeout=1",
    )
    assert ok is False
    assert "fail" in reason.lower() or "unavailable" in reason.lower()


# ── identifier canonicalization (adversarial review: exact-match was fail-open) ─


def test_verbal_stop_in_human_format_blocks_e164_send():
    # Front desk records "(702) 555-0123"; the send path addresses
    # "+17025550123". The ledger must match — a formatting difference must
    # never leak a send past an explicit revocation.
    tenant = _tenant()
    raw_digits = f"702555{uuid.uuid4().int % 10_000:04d}"
    human = f"({raw_digits[:3]}) {raw_digits[3:6]}-{raw_digits[6:]}"
    e164 = f"+1{raw_digits}"
    ingest_manual_revocation(
        tenant_id=tenant, identifier=human, kind="verbal",
        occurred_at=NOW, raw_utterance="asked at front desk", dsn=DSN,
    )
    assert is_suppressed(tenant_id=tenant, identifier=e164, channel="sms", dsn=DSN)
    ok, _ = send_backstop(tenant_id=tenant, identifier=e164, channel="sms", now=NOW, dsn=DSN)
    assert ok is False


def test_mixed_case_email_unsub_blocks_lowercase_send():
    tenant = _tenant()
    local = f"Lead-{uuid.uuid4().hex[:8]}"
    ingest_manual_revocation(
        tenant_id=tenant, identifier=f"{local}@Example.COM", kind="web_form",
        occurred_at=NOW, dsn=DSN,
    )
    assert is_suppressed(
        tenant_id=tenant, identifier=f"{local.lower()}@example.com",
        channel="email", dsn=DSN,
    )


# ── ingest idempotency under webhook retries (adversarial review F6) ─────────


def test_twilio_stop_webhook_retry_does_not_duplicate():
    # A Twilio retry arrives minutes later with no stable timestamp: STOP is
    # permanent, so re-ingesting an already-stopped identifier is a no-op —
    # one suppression row, one memory-supersede chain.
    tenant, phone = _tenant(), _phone()
    _consent(tenant, phone)
    record_preference_memory(
        tenant_id=tenant, identifier=phone,
        content={"kind": "contact_preference", "cadence": "weekly"},
        valid_from=NOW - timedelta(days=30), dsn=DSN,
    )
    a = ingest_twilio_opt_out(
        {"OptOutType": "STOP", "From": phone, "Body": "STOP"},
        tenant_id=tenant, occurred_at=NOW - timedelta(hours=2), dsn=DSN,
    )
    b = ingest_twilio_opt_out(
        {"OptOutType": "STOP", "From": phone, "Body": "STOP"},
        tenant_id=tenant, occurred_at=NOW - timedelta(hours=1), dsn=DSN,
    )
    assert a == b
    with psycopg.connect(DSN, autocommit=True) as conn:
        n = conn.execute(
            "SELECT count(*) FROM suppression_ledger WHERE tenant_id=%s AND identifier=%s",
            (tenant, phone),
        ).fetchone()[0]
    assert n == 1
    rows = get_memories(tenant_id=tenant, identifier=phone, dsn=DSN)
    assert len(rows) == 2  # original + ONE do-not-contact supersede, no second chain


def test_carrier_error_retry_with_sid_not_double_counted():
    tenant, phone = _tenant(), _phone()
    sid = f"SM{uuid.uuid4().hex}"
    for _ in range(2):
        record_carrier_error(
            tenant_id=tenant, identifier=phone, code=30007, occurred_at=NOW,
            provider_sid=sid, dsn=DSN,
        )
    _, count = carrier_30007_spike(
        tenant_id=tenant, window_minutes=60, threshold=5, now=NOW, dsn=DSN,
    )
    assert count == 1


# ── recipient view: the sms_gate feed ────────────────────────────────────────


def test_recipient_view_fail_closed_shape_when_unreachable():
    view = recipient_view(
        tenant_id="t", identifier="+17025550000", channel="sms", now=NOW,
        dsn="postgresql://scalers:scalers@localhost:59999/nope?connect_timeout=1",
    )
    assert view.suppressed is None
    assert view.recent_promo_sends is None
