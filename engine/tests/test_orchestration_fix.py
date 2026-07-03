"""Orchestration-fix P0s (feat/orchestration-fix) — DB-free, hermetic (no network).

Covers two related send-path bugs in the studio outreach flow:

* P2 — the [TEST->] subject marker must apply ONLY in TEST/redirect mode. A real
  live send (worker 'studio_real_send', an operator-approved real send) gets a CLEAN
  subject to the REAL recipient; a redirected/test send gets the '[TEST->{real_to}]'
  marker routed to the operator inbox (the DB row's target stays the real lead).
* P3 — a staged draft must never carry the literal copywriter token '{{unsubscribe}}'
  (resolved to a reply-based opt-out before staging) and must carry a clear CTA; the
  send path additionally REFUSES any body that still holds an unresolved {{...}}.

Nothing here touches Google or Postgres: the Gmail connector is an injected fake and
the action store is monkeypatched to an in-memory stand-in.
"""

from __future__ import annotations

import pytest

import actions.publish as publish
from actions.publish import approve_and_publish
from actions.store import ActionRow
from connectors.gmail import GmailSendResult
from studio.customer_research import build_outreach_draft


@pytest.fixture(autouse=True)
def _legacy_passthrough_env(monkeypatch):
    # wwy.4: declare the legacy 'ladies8391' tenant as passthrough (production
    # sets TEST_MODE_LEGACY_PASSTHROUGH) so the fail-closed registry gate does
    # not refuse the orchestration send behavior under test.
    monkeypatch.setenv("TEST_MODE_LEGACY_PASSTHROUGH", "ladies8391,test_safe_send")


# ── in-memory seams (mirror test_gmail_safe_send.py) ────────────────────────────


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
    """Records exactly what would have been sent — never hits the network."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def send(self, to, subject, body, *, from_addr=None):
        self.calls.append((to, subject, body))
        return GmailSendResult(message_id="m_fake", deep_link="dl_fake")


def _wire(monkeypatch, store: _FakeStore) -> None:
    monkeypatch.setattr(publish, "get_action", store.get_action)
    monkeypatch.setattr(publish, "update_status", store.update_status)
    monkeypatch.setattr(publish, "claim_for_send", store.claim_for_send)


class _FakeCopy:
    def __init__(self, subject: str, body: str) -> None:
        self.subject = subject
        self.body = body


class _FakeCell:
    """Stands in for the gated copywriter email cell — returns a fixed EmailCopy."""

    def __init__(self, copy: _FakeCopy) -> None:
        self._copy = copy

    def run_sync(self, prompt):  # noqa: ARG002 — prompt is unused by the fake
        return self._copy


def _facts() -> dict:
    return {
        "customer_id": "cust_orch_unit",
        "name": "World Tattoo Studio",
        "email": "worldtattoostudio@example.invalid",
        "email_opt_in": True,
        "city": "Denver",
        "persona_traits": {},
        "interests": [],
        "tattoo_history": [],
    }


# ── P2: live = CLEAN subject to real recipient; redirect = [TEST->] to operator ──


def test_live_send_clean_subject_to_real_recipient(monkeypatch):
    """worker 'studio_real_send' is the live path: even with a redirect configured,
    the subject is CLEAN (no [TEST]) and the send goes to the REAL recipient."""
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "operator@inbox.test")
    real_to = "client@studio.example"
    live = ActionRow(
        id="act_live", tenant_id="ladies8391", type="outreach", channel="gmail",
        draft="Hello, a clean body with no token.", status="pending", target=real_to,
        subject="Your consult", worker="studio_real_send",
        idempotency_key="ladies8391:gmail:client:live",
    )
    store = _FakeStore(live)
    _wire(monkeypatch, store)

    gmail = _FakeGmail()
    out = approve_and_publish("act_live", connectors={"gmail": gmail})

    assert out.status == "sent"
    # CLEAN subject, REAL recipient — no [TEST] marker, no redirect.
    assert gmail.calls == [(real_to, "Your consult", "Hello, a clean body with no token.")]
    assert not gmail.calls[0][1].startswith("[TEST")


def test_redirect_send_marks_test_to_operator_inbox(monkeypatch):
    """A non-'studio_real_send' draft with a redirect set is the TEST path: routed to
    the operator inbox with a [TEST->{real_to}] marker; the DB row's target is the
    real lead (honesty)."""
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "operator@inbox.test")
    real_to = "stranger@real.com"
    redirected = ActionRow(
        id="act_redir", tenant_id="ladies8391", type="outreach", channel="gmail",
        draft="Hello, a clean body.", status="pending", target=real_to,
        subject="Promo", worker="team",
        idempotency_key="ladies8391:gmail:stranger:redir",
    )
    store = _FakeStore(redirected)
    _wire(monkeypatch, store)

    gmail = _FakeGmail()
    out = approve_and_publish("act_redir", connectors={"gmail": gmail})

    assert out.status == "sent"
    assert gmail.calls == [("operator@inbox.test", f"[TEST->{real_to}] Promo", "Hello, a clean body.")]
    # Honesty: the DB row's target is UNCHANGED — still the real lead.
    assert out.target == real_to


# ── P3: staged draft carries no {{...}} token + a clear CTA ──────────────────────


def test_staged_draft_resolves_unsubscribe_token(monkeypatch):
    """The LLM copy path returns a body that still carries the required {{unsubscribe}}
    token; build_outreach_draft must RESOLVE it to a reply-based opt-out before staging
    and leave NO {{...}} token, plus add a reply-based CTA (no booking link set)."""
    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "1")
    monkeypatch.delenv("STUDIO_BOOKING_LINK", raising=False)
    monkeypatch.delenv("SCALERS_BOOKING_LINK", raising=False)

    import cells.copywriter as cw

    fake = _FakeCell(_FakeCopy(
        "Hello from one studio to another",
        "Hi there, I run a small studio nearby and wanted to say hello.\n\n"
        "To opt out, just reply here: {{unsubscribe}}",
    ))
    monkeypatch.setattr(cw, "build_copywriter_email_cell", lambda **kw: fake)

    draft = build_outreach_draft(_facts(), goal="introduce our studio", channel="gmail")
    body = draft["draft"] or ""

    assert "{{" not in body and "}}" not in body
    assert "{{unsubscribe}}" not in body
    # Resolved to a concrete reply-based opt-out + a reply-based CTA.
    assert "reply stop" in body.lower()
    assert "reply yes" in body.lower()
    # Original body content survived the resolution.
    assert "wanted to say hello" in body
    assert any(g == "opt_out=reply-based" for g in draft["grounding"])


def test_staged_draft_deterministic_has_cta_and_no_tokens(monkeypatch):
    """The deterministic (no-LLM) path must also stage a token-free body with a clear
    reply-based CTA and a concrete opt-out line."""
    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "0")
    monkeypatch.delenv("STUDIO_BOOKING_LINK", raising=False)
    monkeypatch.delenv("SCALERS_BOOKING_LINK", raising=False)

    draft = build_outreach_draft(_facts(), goal="introduce our studio", channel="gmail")
    body = draft["draft"] or ""

    assert "{{" not in body and "}}" not in body
    assert "reply yes" in body.lower()
    assert "reply stop" in body.lower()


def test_staged_draft_uses_booking_link_when_configured(monkeypatch):
    """When the operator configured a real booking link, the CTA uses it (no fabricated
    URL otherwise)."""
    monkeypatch.setenv("SCALERS_OUTREACH_LLM", "0")
    monkeypatch.setenv("STUDIO_BOOKING_LINK", "https://book.example/ladies")

    draft = build_outreach_draft(_facts(), goal="introduce our studio", channel="gmail")
    body = draft["draft"] or ""

    assert "https://book.example/ladies" in body
    assert any(g == "cta=booking-link" for g in draft["grounding"])


# ── P3: the send path REFUSES an unresolved placeholder ─────────────────────────


def test_send_guard_rejects_unresolved_placeholder(monkeypatch):
    """If a body still contains an unresolved {{...}} token at send time, the publish
    path FAILS honestly with the reason and sends NOTHING."""
    monkeypatch.delenv("GMAIL_REDIRECT_TO", raising=False)
    tok = ActionRow(
        id="act_tok", tenant_id="ladies8391", type="outreach", channel="gmail",
        draft="Hi there.\n\nTo opt out, reply here: {{unsubscribe}}", status="pending",
        target="lead@studio.example", subject="Hello", worker="studio_real_send",
        idempotency_key="ladies8391:gmail:lead:tok",
    )
    store = _FakeStore(tok)
    _wire(monkeypatch, store)

    gmail = _FakeGmail()
    out = approve_and_publish("act_tok", connectors={"gmail": gmail})

    assert out.status == "failed"
    assert "{{unsubscribe}}" in (out.last_error or "")
    # Nothing was sent — the guard fired before any external call.
    assert gmail.calls == []
