"""SMS-2 deterministic compliance gate (CustomerAcq-t90.2, P1).

DB-free, model-free unit coverage of ``compliance.sms_gate``: all 8 checks are
pure code with typed per-recipient block reasons, fail-closed on anything
un-evaluable, identical under TEST-MODE (``SMS_REDIRECT_TO`` cannot bypass), and
exposed at BOTH enforcement points (staging batch partition + a send-time
``(bool, reason)`` eligibility adapter matching the studio send path protocol).

Regression anchor: the client's own platform sent at 00:11–05:03 recipient-local
— every such instant must be blocked for every SDT timezone.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from compliance.sms_gate import (
    BlockCode,
    ConsentRecord,
    GateResult,
    MessageContext,
    RecipientContext,
    SendContext,
    TRUST_TIER_DAILY_LIMITS,
    evaluate_sms,
    gate_at_staging,
    resolve_recipient_timezone,
    sms_send_eligibility,
)

UTC = timezone.utc

# A mid-day instant: 12:00 PDT / 15:00 EDT / 14:00 CDT / 09:00 HST — inside the
# allowed window in every SDT timezone.
NOW = datetime(2026, 7, 2, 19, 0, tzinfo=UTC)

BODY = "SDT: July flash sale this week - book your session. Reply STOP to opt out."
SAMPLES = (
    "SDT: July flash sale this week - book your session. Reply STOP to opt out.",
    "SDT: your artist has an opening tomorrow - book your session. Reply STOP to opt out.",
)


def consent(phone: str) -> ConsentRecord:
    return ConsentRecord(
        phone=phone,
        sms_opt_in=True,
        source="web_form",
        granted_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
    )


def recipient(phone: str = "+17025550123", **overrides) -> RecipientContext:
    base = RecipientContext(
        phone=phone,
        consent=consent(phone),
        suppressed=False,
        recent_promo_sends=(),
        studio_timezone=None,
    )
    return replace(base, **overrides)


def message(body: str = BODY, samples=SAMPLES) -> MessageContext:
    return MessageContext(body=body, registered_samples=samples)


def ctx(now: datetime = NOW, **overrides) -> SendContext:
    base = SendContext(now=now, trust_tier="low", daily_quota_used=0)
    return replace(base, **overrides)


def codes(result: GateResult) -> set[BlockCode]:
    return {b.code for b in result.blocks}


def local(tz: str, y, mo, d, h, mi) -> datetime:
    """A UTC instant that is the given wall-clock time in ``tz``."""
    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(tz)).astimezone(UTC)


# ── happy path ────────────────────────────────────────────────────────────────


def test_clean_message_consented_recipient_midday_passes():
    result = evaluate_sms(recipient(), message(), ctx())
    assert result.allowed
    assert result.blocks == ()


def test_gate_is_deterministic_same_inputs_same_output():
    a = evaluate_sms(recipient(), message(), ctx())
    b = evaluate_sms(recipient(), message(), ctx())
    assert a == b


# ── check 1: consent / PEWC hard block ────────────────────────────────────────


def test_no_consent_row_blocks():
    # The unconsented-list case: a contact with NO consent row is a hard block.
    result = evaluate_sms(recipient(consent=None), message(), ctx())
    assert not result.allowed
    assert BlockCode.NO_CONSENT in codes(result)


def test_consent_without_source_blocks():
    c = replace(consent("+17025550123"), source=None)
    result = evaluate_sms(recipient(consent=c), message(), ctx())
    assert BlockCode.NO_CONSENT in codes(result)


def test_consent_without_timestamp_blocks():
    c = replace(consent("+17025550123"), granted_at=None)
    result = evaluate_sms(recipient(consent=c), message(), ctx())
    assert BlockCode.NO_CONSENT in codes(result)


def test_consent_row_without_sms_opt_in_blocks():
    c = replace(consent("+17025550123"), sms_opt_in=False)
    result = evaluate_sms(recipient(consent=c), message(), ctx())
    assert BlockCode.NO_CONSENT in codes(result)


def test_unconsented_bulk_list_all_blocked_honest_counts():
    # Simulate the 1,093-contact unconsented list at staging: nothing passes and
    # the report's counts are honest (eligible + blocked == total).
    items = [
        (recipient(phone=f"+1702555{i:04d}", consent=None), message())
        for i in range(25)
    ]
    report = gate_at_staging(items, ctx())
    assert report.n_eligible == 0
    assert report.n_blocked == 25
    assert all(
        BlockCode.NO_CONSENT in {b.code for b in e.result.blocks}
        for e in report.entries
    )


# ── check 2: suppression ledger ───────────────────────────────────────────────


def test_suppressed_recipient_blocks():
    result = evaluate_sms(recipient(suppressed=True), message(), ctx())
    assert BlockCode.SUPPRESSED in codes(result)


def test_suppression_ledger_unavailable_blocks_fail_closed():
    result = evaluate_sms(recipient(suppressed=None), message(), ctx())
    assert not result.allowed
    assert BlockCode.SUPPRESSION_UNEVALUABLE in codes(result)


# ── check 3: quiet hours by recipient timezone ───────────────────────────────

SDT_TZ_AREA_CODES = [
    ("702", "America/Los_Angeles"),  # Las Vegas — Pacific
    ("212", "America/New_York"),  # Eastern
    ("808", "Pacific/Honolulu"),  # Hawaii
    ("713", "America/Chicago"),  # Central
]


@pytest.mark.parametrize("npa,tz", SDT_TZ_AREA_CODES)
def test_resolve_recipient_timezone_by_area_code(npa, tz):
    resolved = resolve_recipient_timezone(f"+1{npa}5550123", studio_timezone=None)
    assert resolved is not None
    assert tz in resolved.zones


@pytest.mark.parametrize("npa,tz", SDT_TZ_AREA_CODES)
@pytest.mark.parametrize("hh,mm", [(0, 11), (2, 45), (5, 3)])
def test_client_platform_00_11_to_05_03_regression_blocked_everywhere(npa, tz, hh, mm):
    # The client's platform sent 00:11–05:03 recipient-local. We block that in
    # every SDT timezone — never copy it.
    when = local(tz, 2026, 7, 2, hh, mm)
    result = evaluate_sms(recipient(phone=f"+1{npa}5550123"), message(), ctx(now=when))
    assert not result.allowed
    assert BlockCode.QUIET_HOURS in codes(result)


@pytest.mark.parametrize("npa,tz", SDT_TZ_AREA_CODES)
def test_midday_allowed_in_every_sdt_timezone(npa, tz):
    when = local(tz, 2026, 7, 2, 12, 0)
    result = evaluate_sms(recipient(phone=f"+1{npa}5550123"), message(), ctx(now=when))
    assert result.allowed, result.blocks


def test_federal_window_boundaries_pacific():
    # Allowed is [8am, 9pm) recipient-local under the federal rule.
    for h, m, ok in [(7, 59, False), (8, 0, True), (20, 59, True), (21, 0, False)]:
        when = local("America/Los_Angeles", 2026, 7, 2, h, m)
        result = evaluate_sms(recipient(), message(), ctx(now=when))
        assert result.allowed is ok, f"{h:02d}:{m:02d} expected allowed={ok}"


@pytest.mark.parametrize(
    "npa,tz",
    [
        ("305", "America/New_York"),  # FL
        ("405", "America/Chicago"),  # OK
        ("206", "America/Los_Angeles"),  # WA
    ],
)
def test_fl_ok_wa_overlay_blocks_after_8pm_local(npa, tz):
    when = local(tz, 2026, 7, 2, 20, 30)  # 8:30pm local — fine federally, not in FL/OK/WA
    result = evaluate_sms(recipient(phone=f"+1{npa}5550123"), message(), ctx(now=when))
    assert not result.allowed
    assert BlockCode.QUIET_HOURS in codes(result)


def test_non_overlay_state_still_allowed_at_2030_local():
    when = local("America/New_York", 2026, 7, 2, 20, 30)
    result = evaluate_sms(recipient(phone="+12125550123"), message(), ctx(now=when))
    assert result.allowed, result.blocks


def test_hawaii_no_dst_same_wall_clock_year_round():
    # Pacific/Honolulu never shifts: 8:05am HST is allowed in July AND January.
    for mo in (7, 1):
        when = local("Pacific/Honolulu", 2026, mo, 2, 8, 5)
        result = evaluate_sms(recipient(phone="+18085550123"), message(), ctx(now=when))
        assert result.allowed, (mo, result.blocks)


def test_split_timezone_area_code_uses_most_restrictive():
    # FL 850 spans Eastern AND Central. 8:30am EDT is 7:30am CDT — fail-closed
    # means blocked unless the instant is inside the window in EVERY candidate.
    when = datetime(2026, 7, 2, 12, 30, tzinfo=UTC)
    result = evaluate_sms(recipient(phone="+18505550123"), message(), ctx(now=when))
    assert not result.allowed
    assert BlockCode.QUIET_HOURS in codes(result)


def test_unknown_area_code_without_affinity_blocks_tz_unresolvable():
    result = evaluate_sms(recipient(phone="+19995550123"), message(), ctx())
    assert not result.allowed
    assert BlockCode.TZ_UNRESOLVABLE in codes(result)


def test_unknown_area_code_with_studio_affinity_uses_restrictive_window():
    # Affinity fallback resolves the clock, but the state is unknown — so the
    # stricter 8am–8pm window applies. 8:30pm local via affinity is blocked...
    when = local("America/Los_Angeles", 2026, 7, 2, 20, 30)
    r = recipient(phone="+19995550123", studio_timezone="America/Los_Angeles")
    result = evaluate_sms(r, message(), ctx(now=when))
    assert BlockCode.QUIET_HOURS in codes(result)
    # ...while midday passes.
    result = evaluate_sms(r, message(), ctx(now=local("America/Los_Angeles", 2026, 7, 2, 12, 0)))
    assert result.allowed, result.blocks


def test_naive_now_blocks_fail_closed():
    result = evaluate_sms(recipient(), message(), ctx(now=datetime(2026, 7, 2, 19, 0)))
    assert not result.allowed
    assert BlockCode.TIME_UNEVALUABLE in codes(result)


# ── check 4: opt-out language ─────────────────────────────────────────────────


def test_missing_opt_out_language_blocks():
    body = "SDT: July flash sale this week - book your session."
    samples = ("SDT: July flash sale this week - book your session.",)
    result = evaluate_sms(recipient(), message(body=body, samples=samples), ctx())
    assert BlockCode.MISSING_OPT_OUT_LANGUAGE in codes(result)


def test_opt_out_language_case_insensitive():
    body = BODY.replace("Reply STOP", "reply stop")
    result = evaluate_sms(recipient(), message(body=body), ctx())
    assert BlockCode.MISSING_OPT_OUT_LANGUAGE not in codes(result)


# ── check 5: SHAFT lint + shorteners + prohibited lending ────────────────────


@pytest.mark.parametrize("term", ["free whiskey", "vape pens", "casino night", "CBD balm"])
def test_shaft_terms_block(term):
    body = f"SDT: {term} with every session this week. Reply STOP to opt out."
    result = evaluate_sms(recipient(), message(body=body), ctx())
    assert BlockCode.SHAFT_TERM in codes(result)


def test_public_url_shortener_blocks():
    body = "SDT: book now https://bit.ly/sdt-july - Reply STOP to opt out."
    result = evaluate_sms(recipient(), message(body=body), ctx())
    assert BlockCode.URL_SHORTENER in codes(result)


def test_payday_lending_language_blocks():
    body = "SDT: no cash? get a payday loan and book today. Reply STOP to opt out."
    result = evaluate_sms(recipient(), message(body=body), ctx())
    assert BlockCode.PROHIBITED_LENDING in codes(result)


# ── check 6: registered 10DLC sample consistency ─────────────────────────────


def test_message_unlike_any_registered_sample_blocks():
    body = "Huge crypto giveaway!!! claim your prize wallet now. Reply STOP to opt out."
    result = evaluate_sms(recipient(), message(body=body), ctx())
    assert BlockCode.SAMPLE_MISMATCH in codes(result)


def test_no_registered_samples_blocks_fail_closed():
    result = evaluate_sms(recipient(), message(samples=None), ctx())
    assert not result.allowed
    assert BlockCode.SAMPLES_UNAVAILABLE in codes(result)
    result = evaluate_sms(recipient(), message(samples=()), ctx())
    assert BlockCode.SAMPLES_UNAVAILABLE in codes(result)


def test_bnpl_term_not_in_registered_samples_blocks():
    body = "SDT: July flash sale this week - book your session with Klarna. Reply STOP to opt out."
    result = evaluate_sms(recipient(), message(body=body), ctx())
    assert BlockCode.BNPL_NOT_REGISTERED in codes(result)


def test_bnpl_term_present_in_registered_samples_passes():
    body = "SDT: July flash sale this week - book your session with Klarna. Reply STOP to opt out."
    samples = SAMPLES + (
        "SDT: July flash sale this week - book your session with Klarna. Reply STOP to opt out.",
    )
    result = evaluate_sms(recipient(), message(body=body, samples=samples), ctx())
    assert BlockCode.BNPL_NOT_REGISTERED not in codes(result)
    assert result.allowed, result.blocks


# ── check 7: per-contact frequency cap ───────────────────────────────────────


def test_promo_within_72h_window_blocks():
    sends = (NOW - timedelta(hours=71),)
    result = evaluate_sms(recipient(recent_promo_sends=sends), message(), ctx())
    assert BlockCode.FREQUENCY_CAP in codes(result)


def test_promo_older_than_72h_passes():
    sends = (NOW - timedelta(hours=73),)
    result = evaluate_sms(recipient(recent_promo_sends=sends), message(), ctx())
    assert result.allowed, result.blocks


def test_send_history_unavailable_blocks_fail_closed():
    result = evaluate_sms(recipient(recent_promo_sends=None), message(), ctx())
    assert BlockCode.FREQUENCY_UNEVALUABLE in codes(result)


def test_naive_send_history_timestamp_blocks_fail_closed():
    sends = (datetime(2026, 7, 1, 12, 0),)  # naive — cannot be compared safely
    result = evaluate_sms(recipient(recent_promo_sends=sends), message(), ctx())
    assert BlockCode.FREQUENCY_UNEVALUABLE in codes(result)


# ── check 8: pacing to trust-score tier ──────────────────────────────────────


def test_daily_quota_exhausted_blocks():
    limit = TRUST_TIER_DAILY_LIMITS["low"]
    result = evaluate_sms(recipient(), message(), ctx(daily_quota_used=limit))
    assert BlockCode.PACING_EXCEEDED in codes(result)


def test_daily_quota_under_limit_passes():
    limit = TRUST_TIER_DAILY_LIMITS["low"]
    result = evaluate_sms(recipient(), message(), ctx(daily_quota_used=limit - 1))
    assert result.allowed, result.blocks


def test_unknown_trust_tier_blocks_fail_closed():
    result = evaluate_sms(recipient(), message(), ctx(trust_tier=None))
    assert BlockCode.PACING_UNEVALUABLE in codes(result)
    result = evaluate_sms(recipient(), message(), ctx(trust_tier="platinum"))
    assert BlockCode.PACING_UNEVALUABLE in codes(result)


def test_unknown_quota_blocks_fail_closed():
    result = evaluate_sms(recipient(), message(), ctx(daily_quota_used=None))
    assert BlockCode.PACING_UNEVALUABLE in codes(result)


# ── all blocks collected (honest reasons, not first-fail) ────────────────────


def test_multiple_violations_all_reported():
    body = "vape deals https://bit.ly/x"
    result = evaluate_sms(
        recipient(consent=None, suppressed=True), message(body=body), ctx()
    )
    got = codes(result)
    assert {
        BlockCode.NO_CONSENT,
        BlockCode.SUPPRESSED,
        BlockCode.MISSING_OPT_OUT_LANGUAGE,
        BlockCode.SHAFT_TERM,
        BlockCode.URL_SHORTENER,
        BlockCode.SAMPLE_MISMATCH,
    } <= got


# ── enforcement points: staging partition + send-time eligibility adapter ────


def test_staging_partitions_eligible_and_blocked():
    items = [
        (recipient(), message()),
        (recipient(phone="+17025550999", consent=None), message()),
    ]
    report = gate_at_staging(items, ctx())
    assert report.n_eligible == 1
    assert report.n_blocked == 1
    assert report.n_eligible + report.n_blocked == len(items)
    blocked = [e for e in report.entries if not e.result.allowed]
    assert blocked[0].phone == "+17025550999"


def test_send_time_eligibility_adapter_protocol():
    ok, reason = sms_send_eligibility(recipient(), message(), ctx())
    assert ok is True
    assert isinstance(reason, str) and reason
    ok, reason = sms_send_eligibility(recipient(consent=None), message(), ctx())
    assert ok is False
    assert "no_consent" in reason


# ── TEST-MODE: sandbox cannot bypass the gate ────────────────────────────────


def test_sms_redirect_to_does_not_change_gate_output(monkeypatch):
    blocked_recipient = recipient(consent=None)
    monkeypatch.delenv("SMS_REDIRECT_TO", raising=False)
    without_env_clean = evaluate_sms(recipient(), message(), ctx())
    without_env_blocked = evaluate_sms(blocked_recipient, message(), ctx())

    monkeypatch.setenv("SMS_REDIRECT_TO", "+15550000000")
    with_env_clean = evaluate_sms(recipient(), message(), ctx())
    with_env_blocked = evaluate_sms(blocked_recipient, message(), ctx())

    assert without_env_clean == with_env_clean
    assert without_env_blocked == with_env_blocked
    assert not with_env_blocked.allowed
