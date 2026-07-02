"""SMS-1 SmsConnector seam (CustomerAcq-t90.1) — DB-free unit coverage.

The provider-agnostic protocol, the Reply-STOP footer, the Twilio webhook
signature check, and every refusal that happens BEFORE the ledger/HTTP are
touched: disabled gate, template placeholders, and the fail-closed
missing-redirect sandbox default (a lost env var can never cause a real send).
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from connectors.base import ConnectorDisabledError
from connectors.sms import (
    SmsConnector,
    SmsSendStatus,
    TwilioSmsConnector,
    finalize_sms_body,
    parse_opt_out_event,
    verify_twilio_signature,
)
from research.providers.firecrawl import HttpResponse
from research.safety import OFFICIAL_API_HOSTS


class _FakeFetcher:
    def __init__(self, body='{"sid": "SM123", "status": "queued"}', status=201):
        self.calls = []
        self._body, self._status = body, status

    def request(self, *, method, ip, host, path, headers, body, timeout):
        self.calls.append({"method": method, "ip": ip, "host": host, "path": path,
                           "headers": headers, "body": body})
        return HttpResponse(status=self._status, body=self._body)


def _resolver(ip="93.184.216.34"):
    def r(host, port, *a, **k):
        return [(None, None, None, "", (ip, port))]
    return r


def _conn(**kw):
    kw.setdefault("account_sid", "ACxxxxxxxx")
    kw.setdefault("auth_token", "secret-token")
    kw.setdefault("messaging_service_sid", "MGxxxxxxxx")
    kw.setdefault("fetcher", _FakeFetcher())
    kw.setdefault("resolver", _resolver())
    return TwilioSmsConnector(enabled=True, **kw)


# ── protocol + registry ──────────────────────────────────────────────────────


def test_twilio_connector_satisfies_provider_agnostic_protocol():
    assert isinstance(_conn(), SmsConnector)


def test_twilio_host_is_allowlisted_official_api():
    assert "api.twilio.com" in OFFICIAL_API_HOSTS["twilio"]


# ── Reply-STOP footer ────────────────────────────────────────────────────────


def test_finalize_appends_opt_out_footer_when_missing():
    out = finalize_sms_body("SDT: July flash sale - book now.")
    assert "reply stop" in out.lower()


def test_finalize_keeps_existing_opt_out_language():
    body = "SDT: July flash sale. Reply STOP to opt out."
    assert finalize_sms_body(body) == body


# ── refusals that must happen BEFORE ledger/HTTP ─────────────────────────────


def test_disabled_by_default_refuses():
    c = TwilioSmsConnector(
        account_sid="AC", auth_token="t", messaging_service_sid="MG"
    )  # enabled defaults False
    assert c.enabled is False
    with pytest.raises(ConnectorDisabledError):
        c.send_sms(tenant_id="t", to="+17025550123", body="hi")


def test_template_placeholder_blocked_no_dispatch(monkeypatch):
    monkeypatch.setenv("SMS_REDIRECT_TO", "+15550001111")
    fake = _FakeFetcher()
    c = _conn(fetcher=fake)
    result = c.send_sms(
        tenant_id="t", to="+17025550123", body="Hi {{first_name}}, book now.",
    )
    assert result.status is SmsSendStatus.BLOCKED
    assert "placeholder" in result.reason.lower()
    assert fake.calls == []


def test_missing_redirect_fails_closed_not_live(monkeypatch):
    # SANDBOX DEFAULT: no SMS_REDIRECT_TO and no explicit live authorization
    # means REFUSE — a missing env var can never turn into a real send.
    monkeypatch.delenv("SMS_REDIRECT_TO", raising=False)
    fake = _FakeFetcher()
    c = _conn(fetcher=fake)
    result = c.send_sms(tenant_id="t", to="+17025550123", body="hello")
    assert result.status is SmsSendStatus.BLOCKED
    assert "redirect" in result.reason.lower()
    assert fake.calls == []


# ── Twilio webhook signature (Advanced Opt-Out feed) ─────────────────────────


def _sign(auth_token: str, url: str, params: dict[str, str]) -> str:
    data = url + "".join(k + v for k, v in sorted(params.items()))
    digest = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def test_signature_verifies_and_rejects_tamper():
    token = "secret-token"
    url = "https://hooks.example.com/sms/optout"
    params = {"From": "+17025550123", "OptOutType": "STOP", "Body": "STOP"}
    good = _sign(token, url, params)
    assert verify_twilio_signature(token, url, params, good) is True
    assert verify_twilio_signature(token, url, params, good + "x") is False
    tampered = {**params, "From": "+19995550000"}
    assert verify_twilio_signature(token, url, tampered, good) is False


def test_parse_opt_out_event_only_stop():
    assert parse_opt_out_event({"OptOutType": "STOP", "From": "+17025550123"}) is not None
    assert parse_opt_out_event({"OptOutType": "START", "From": "+17025550123"}) is None
    assert parse_opt_out_event({"OptOutType": "HELP", "From": "+17025550123"}) is None
