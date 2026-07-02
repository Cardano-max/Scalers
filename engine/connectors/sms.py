"""SMS connector seam (CustomerAcq-t90.1, blueprint §2-B1/§4.2) — gated, sandbox-default.

Twilio Messages API behind a thin provider-agnostic :class:`SmsConnector`
protocol: NOTHING Twilio-specific leaves this module, so a provider swap
(Telnyx later) is a config change, not new code paths. Uses a Twilio
**Messaging Service** (``MessagingServiceSid`` — sender pool, Sticky Sender,
status callbacks) and **Advanced Opt-Out** (OptOutType webhooks feed the
cross-channel suppression ledger).

Send-path order is LOAD-BEARING (each step fails closed, no dispatch):

1. ``_require_enabled`` — mock-default OFF, like every gated connector.
2. Reply-STOP footer via :func:`finalize_sms_body` (every SMS carries it).
3. ``{{placeholder}}`` guard — a raw template token never leaves the building.
4. Sandbox default: with no explicit live authorization, ``SMS_REDIRECT_TO``
   MUST be set and every dispatch goes to it (body marked ``[TEST->real]``);
   redirect unset = REFUSE. A missing env var can never cause a real send.
   The live flip is the go-live bead (t90.4), out of scope here.
5. **HARD REQUIREMENT (t90.3 / 04d0573):** :func:`suppression.ledger.claim_send_slot`
   — atomic suppression + frequency-window claim — runs BEFORE any Twilio
   call. Twilio has NO provider-side idempotency; the slot claim is the only
   exactly-once guarantee. A refused claim means ZERO HTTP. The claim is made
   against the REAL recipient even under redirect, so sandbox sends consume
   the window (TEST-MODE proves the machinery).
6. Only then the Messages API POST via the de6 secure boundary
   (official-host allowlist + pin-to-IP; Basic auth in the header, never a
   URL, never logged).

The full 8-check compliance gate runs at send time through
:func:`send_sms_gated` — the composition the (phase3) ``publish_action``
``channel=='sms'`` branch calls; on trunk it IS the send-time enforcement
point required by t90.2 AC-4.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, Sequence, runtime_checkable
from urllib.parse import urlencode

from compliance.sms_gate import GateResult, MessageContext, SendContext, evaluate_sms
from connectors.base import GatedConnector, redact
from sideeffects.keys import Channel, idempotency_key
from sideeffects.provider import ProviderResult
from suppression.ledger import (
    claim_send_slot,
    ingest_twilio_opt_out,
    recipient_context_for_gate,
    record_carrier_error,
    record_delivery_event,
)

__all__ = [
    "SmsConnector",
    "SmsSendResult",
    "SmsSendStatus",
    "SmsWebhookSignatureError",
    "TwilioSmsConnector",
    "finalize_sms_body",
    "ingest_opt_out_webhook",
    "ingest_status_callback",
    "parse_opt_out_event",
    "send_sms_gated",
    "verify_twilio_signature",
]

TWILIO_API_BASE = "https://api.twilio.com"
_TWILIO_HOST = "api.twilio.com"

_OPT_OUT_RE = re.compile(r"\b(?:reply|text)\s+stop\b", re.IGNORECASE)
_PLACEHOLDER_RE = re.compile(r"\{\{[^{}]*\}\}")
_OPT_OUT_FOOTER = "Reply STOP to opt out."

# Carrier error codes the status-callback ingest routes to the ledger
# (30003-30006 auto-suppress there; 30007 feeds the spike alert).
_CARRIER_ERROR_CODES = frozenset({30003, 30004, 30005, 30006, 30007})


class SmsWebhookSignatureError(ValueError):
    """An inbound Twilio webhook failed X-Twilio-Signature verification —
    nothing from it may be ingested."""


class SmsSendStatus(Enum):
    SENT = "sent"          # dispatched to the provider (live or redirect)
    BLOCKED = "blocked"    # refused BEFORE any HTTP (typed reason; no side effect beyond a won claim)
    FAILED = "failed"      # provider dispatch failed AFTER the slot claim (fail-closed: under-send)


@dataclass(frozen=True)
class SmsSendResult:
    """The connector's honest verdict for one send attempt. ``real_to`` is the
    intended recipient; ``dispatched_to`` is where the message actually went
    (the redirect number in sandbox) — the two differing is the SAFETY, not a
    bug."""

    status: SmsSendStatus
    mode: str | None            # live | test_redirect | None (blocked before mode)
    reason: str
    real_to: str
    dispatched_to: str | None = None
    provider: ProviderResult | None = None


@runtime_checkable
class SmsConnector(Protocol):
    """Provider-agnostic SMS seam: everything above this protocol is
    provider-blind; only the adapter behind it knows Twilio exists."""

    def send_sms(
        self,
        *,
        tenant_id: str,
        to: str,
        body: str,
        kind: str = "promo",
        live: bool = False,
        now: datetime | None = None,
        dsn: str | None = None,
    ) -> SmsSendResult: ...


def finalize_sms_body(body: str) -> str:
    """Every outbound SMS carries opt-out language: append the Reply-STOP
    footer unless the body already has it (the trunk equivalent of the studio
    path's ``_finalize_outreach_body``)."""
    if _OPT_OUT_RE.search(body):
        return body
    return f"{body.rstrip()} {_OPT_OUT_FOOTER}"


def verify_twilio_signature(
    auth_token: str, url: str, params: dict[str, str], signature: str | None
) -> bool:
    """Validate an X-Twilio-Signature header (HMAC-SHA1 over the webhook URL +
    the alphabetically-sorted form params, base64) in constant time."""
    if not signature:
        return False
    data = url + "".join(k + v for k, v in sorted(params.items()))
    digest = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)


class TwilioSmsConnector(GatedConnector):
    """Twilio Messages API adapter (the ONLY module that knows Twilio).
    Disabled by default; enabled it still cannot reach a real recipient while
    ``SMS_REDIRECT_TO`` is set (sandbox default until the go-live bead)."""

    name = "sms"
    provider_name = "twilio"

    def __init__(
        self,
        *,
        account_sid: str | None = None,
        auth_token: str | None = None,
        messaging_service_sid: str | None = None,
        status_callback_url: str | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._messaging_service_sid = messaging_service_sid
        self._status_callback_url = status_callback_url

    def __repr__(self) -> str:  # never leak the token in a repr
        return (
            f"TwilioSmsConnector(enabled={self._enabled},"
            f" token={redact(self._auth_token)})"
        )

    def send_sms(
        self,
        *,
        tenant_id: str,
        to: str,
        body: str,
        kind: str = "promo",
        live: bool = False,
        now: datetime | None = None,
        dsn: str | None = None,
    ) -> SmsSendResult:
        """Send one SMS through the full fail-closed pipeline (module
        docstring order). ``live=True`` is the go-live bead's explicit
        authorization and is NOT exercised in this bead — the default always
        redirects or refuses."""
        self._require_enabled()
        final_body = finalize_sms_body(body)

        hit = _PLACEHOLDER_RE.search(final_body)
        if hit:
            return SmsSendResult(
                SmsSendStatus.BLOCKED, None,
                f"refusing to send: unresolved template placeholder {hit.group(0)!r}",
                real_to=to,
            )

        redirect = os.environ.get("SMS_REDIRECT_TO")
        if live:
            mode, dispatch_to, dispatch_body = "live", to, final_body
        elif redirect:
            mode = "test_redirect"
            dispatch_to = redirect
            dispatch_body = f"[TEST->{to}] {final_body}"
        else:
            return SmsSendResult(
                SmsSendStatus.BLOCKED, None,
                "SMS_REDIRECT_TO not configured and no explicit live authorization"
                " — refusing, fail closed (sandbox default)",
                real_to=to,
            )

        # HARD REQUIREMENT: atomic slot claim BEFORE any Twilio call. Keyed on
        # the clean finalized body (not the [TEST->] variant) so the same
        # logical message dedupes identically in sandbox and live.
        key = idempotency_key(tenant_id, Channel.SMS, to, final_body)
        ok, reason = claim_send_slot(
            tenant_id=tenant_id, identifier=to, channel="sms", kind=kind,
            mode=mode, idempotency_key=key, now=now, dsn=dsn,
        )
        if not ok:
            return SmsSendResult(SmsSendStatus.BLOCKED, mode, reason, real_to=to)

        form: dict[str, str] = {
            "To": dispatch_to,
            "Body": dispatch_body,
            "MessagingServiceSid": self._messaging_service_sid or "",
        }
        if self._status_callback_url:
            form["StatusCallback"] = self._status_callback_url
        auth = base64.b64encode(
            f"{self._account_sid}:{self._auth_token}".encode()
        ).decode()
        resp = self._secure_request(
            api_base=TWILIO_API_BASE,
            host=_TWILIO_HOST,
            method="POST",
            path=f"/2010-04-01/Accounts/{self._account_sid}/Messages.json",
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            body=urlencode(form).encode(),
        )
        if resp.status not in (200, 201):
            # The slot stays consumed: an uncertain provider outcome must
            # under-send, never risk a double send (fail-closed, W2).
            return SmsSendResult(
                SmsSendStatus.FAILED, mode,
                f"provider dispatch failed (HTTP {resp.status}); send slot stays"
                " consumed — fail closed",
                real_to=to, dispatched_to=dispatch_to,
            )
        data = json.loads(resp.body or "{}")
        sid = data.get("sid")
        return SmsSendResult(
            SmsSendStatus.SENT, mode, "sent", real_to=to, dispatched_to=dispatch_to,
            provider=ProviderResult(
                provider_id=sid, external_id=sid, extra={"mode": mode},
            ),
        )


def send_sms_gated(
    connector: SmsConnector,
    *,
    tenant_id: str,
    to: str,
    body: str,
    registered_samples: Sequence[str] | None,
    trust_tier: str | None,
    daily_quota_used: int | None,
    kind: str = "promo",
    studio_timezone: str | None = None,
    now: datetime,
    dsn: str | None = None,
    live: bool = False,
) -> tuple[GateResult, SmsSendResult | None]:
    """The SEND-TIME enforcement composition (t90.2 AC-4): ledger-fed
    :func:`recipient_context_for_gate` → the full deterministic 8-check
    compliance gate → only an allowed draft reaches the connector (which then
    re-checks atomically via the slot claim). Returns ``(gate_result, send)``;
    ``send is None`` means the gate blocked and NOTHING was dispatched. This
    is the body of the (phase3) ``publish_action`` sms branch."""
    final_body = finalize_sms_body(body)
    recipient = recipient_context_for_gate(
        tenant_id=tenant_id, phone=to, studio_timezone=studio_timezone,
        now=now, dsn=dsn,
    )
    gate = evaluate_sms(
        recipient,
        MessageContext(
            body=final_body,
            registered_samples=tuple(registered_samples) if registered_samples else None,
        ),
        SendContext(now=now, trust_tier=trust_tier, daily_quota_used=daily_quota_used),
    )
    if not gate.allowed:
        return gate, None
    return gate, connector.send_sms(
        tenant_id=tenant_id, to=to, body=final_body, kind=kind, live=live,
        now=now, dsn=dsn,
    )


# ── inbound webhooks: Advanced Opt-Out + status callbacks ────────────────────


def parse_opt_out_event(form: dict[str, Any]) -> dict[str, str] | None:
    """Extract the STOP payload from a Twilio Advanced Opt-Out webhook.
    ``START``/``HELP`` return ``None`` — they are not suppressions (opt-back-in
    stays an operator decision)."""
    if (form.get("OptOutType") or "").strip().upper() != "STOP":
        return None
    return {"from": (form.get("From") or "").strip(), "body": form.get("Body") or ""}


def ingest_opt_out_webhook(
    form: dict[str, str],
    *,
    tenant_id: str,
    url: str,
    signature: str | None,
    auth_token: str,
    occurred_at: datetime | None = None,
    dsn: str | None = None,
) -> int | None:
    """Verify and mirror one Advanced Opt-Out webhook into the suppression
    ledger (the atomic STOP transaction: suppression + consent revocation +
    memory supersede). A bad signature raises — nothing is ingested from an
    unauthenticated webhook."""
    if not verify_twilio_signature(auth_token, url, form, signature):
        raise SmsWebhookSignatureError(
            "X-Twilio-Signature verification failed — dropping opt-out webhook"
        )
    if parse_opt_out_event(form) is None:
        return None
    return ingest_twilio_opt_out(
        form, tenant_id=tenant_id, occurred_at=occurred_at, dsn=dsn
    )


def ingest_status_callback(
    form: dict[str, str],
    *,
    tenant_id: str,
    url: str | None = None,
    signature: str | None = None,
    auth_token: str | None = None,
    occurred_at: datetime | None = None,
    dsn: str | None = None,
) -> int:
    """Ingest one Messaging Service status callback into ``delivery_events``
    (retry-idempotent per (sid, status)); carrier error codes are routed to the
    ledger (30003-30006 auto-suppress, 30007 feeds the spike alert). Signature
    verification applies whenever ``auth_token`` is provided (sandbox stubs may
    omit it — AC-5 allows a stub while no public webhook endpoint exists)."""
    if auth_token is not None and not verify_twilio_signature(
        auth_token, url or "", form, signature
    ):
        raise SmsWebhookSignatureError(
            "X-Twilio-Signature verification failed — dropping status callback"
        )
    sid = form.get("MessageSid")
    status = (form.get("MessageStatus") or "unknown").lower()
    identifier = form.get("To") or ""
    error_code = int(form["ErrorCode"]) if form.get("ErrorCode") else None
    event_id = record_delivery_event(
        tenant_id=tenant_id, identifier=identifier, status=status,
        provider_sid=sid, error_code=error_code, raw=dict(form),
        occurred_at=occurred_at, dsn=dsn,
    )
    if error_code in _CARRIER_ERROR_CODES:
        record_carrier_error(
            tenant_id=tenant_id, identifier=identifier, code=error_code,
            occurred_at=occurred_at, provider_sid=sid, dsn=dsn,
        )
    return event_id
