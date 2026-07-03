"""Safe Gmail send tests: SAFE-TEST-REDIRECT + atomic exactly-once CLAIM.

Both changes live in :mod:`actions.publish`. Nothing here ever touches Google —
``GmailConnector.send`` is monkeypatched / injected as a fake, so no real email is
sent. Test 1 runs DB-free against an in-memory store; test 2 runs against the real
Postgres so the atomic ``pending``→``sending`` claim is exercised for real (a single
conditional UPDATE is the only thing that can make exactly-once true under retry /
concurrency — an in-memory fake could not prove it).
"""

from __future__ import annotations

import threading
import uuid

import pytest

import actions.publish as publish
from actions.publish import approve_and_publish
from actions.store import ActionRow
from connectors.gmail import GmailConnector, GmailSendResult

_DSN = "postgresql://scalers:scalers@localhost:5432/scalers"


@pytest.fixture(autouse=True)
def _legacy_passthrough_env(monkeypatch):
    # wwy.4: declare the legacy 'ladies8391'/'test_safe_send' tenants as
    # passthrough (production sets TEST_MODE_LEGACY_PASSTHROUGH) so the
    # fail-closed registry gate does not refuse the send behavior under test.
    monkeypatch.setenv("TEST_MODE_LEGACY_PASSTHROUGH", "ladies8391,test_safe_send")


# ── CHANGE 1: SAFE TEST REDIRECT ────────────────────────────────────────────────


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


def test_redirect_routes_to_env_inbox_and_leaves_target_unchanged(monkeypatch):
    real_to = "client@studio.example"
    redirect = "qa-inbox@example.com"
    monkeypatch.setenv("GMAIL_REDIRECT_TO", redirect)

    row = ActionRow(
        id="act_redir", tenant_id="ladies8391", type="outreach", channel="gmail",
        draft="Hello from Ladies First", status="pending", target=real_to,
        subject="Your custom piece",
        idempotency_key="ladies8391:gmail:client@studio.example:redir",
    )
    store = _FakeStore(row)
    monkeypatch.setattr(publish, "get_action", store.get_action)
    monkeypatch.setattr(publish, "update_status", store.update_status)
    monkeypatch.setattr(publish, "claim_for_send", store.claim_for_send)

    gmail = _FakeGmail()
    out = approve_and_publish("act_redir", connectors={"gmail": gmail})

    # Send was redirected to the env inbox, subject prefixed with the intended lead.
    assert gmail.calls == [
        (redirect, f"[TEST->{real_to}] Your custom piece", "Hello from Ladies First")
    ]
    # Honesty: the DB row's target is UNCHANGED — still the real lead.
    assert out.target == real_to
    assert out.status == "sent"


def test_no_redirect_env_fails_closed(monkeypatch):
    # CRITICAL send-safety (wwy.3): a MISSING GMAIL_REDIRECT_TO must NOT turn a
    # routine approve into a live send to the real recipient. With no redirect
    # configured and no explicit live authorization, the send is REFUSED
    # (status=failed, mode=blocked) and ZERO Gmail calls are made.
    monkeypatch.delenv("GMAIL_REDIRECT_TO", raising=False)
    real_to = "client@studio.example"
    row = ActionRow(
        id="act_noredir", tenant_id="ladies8391", type="outreach", channel="gmail",
        draft="Hi", status="pending", target=real_to, subject="Your custom piece",
        idempotency_key="ladies8391:gmail:client@studio.example:noredir",
    )
    store = _FakeStore(row)
    monkeypatch.setattr(publish, "get_action", store.get_action)
    monkeypatch.setattr(publish, "update_status", store.update_status)
    monkeypatch.setattr(publish, "claim_for_send", store.claim_for_send)

    gmail = _FakeGmail()
    out = approve_and_publish("act_noredir", connectors={"gmail": gmail})

    assert gmail.calls == []  # never reached the network — fail closed
    assert out.status == "failed"
    assert getattr(out, "mode", None) == "blocked"
    assert "GMAIL_REDIRECT_TO not configured" in (out.last_error or "")
    assert out.target == real_to  # the real lead is never mutated


def test_no_redirect_but_live_authorized_still_sends(monkeypatch):
    # The ONLY way a no-redirect send reaches the real recipient: an EXPLICIT
    # operator live authorization (worker == 'studio_real_send'). Then it sends
    # live with a CLEAN subject — no [TEST] prefix.
    monkeypatch.delenv("GMAIL_REDIRECT_TO", raising=False)
    real_to = "client@studio.example"
    row = ActionRow(
        id="act_live", tenant_id="ladies8391", type="outreach", channel="gmail",
        worker="studio_real_send", draft="Hi", status="pending", target=real_to,
        subject="Your custom piece",
        idempotency_key="ladies8391:gmail:client@studio.example:live",
    )
    store = _FakeStore(row)
    monkeypatch.setattr(publish, "get_action", store.get_action)
    monkeypatch.setattr(publish, "update_status", store.update_status)
    monkeypatch.setattr(publish, "claim_for_send", store.claim_for_send)

    gmail = _FakeGmail()
    out = approve_and_publish("act_live", connectors={"gmail": gmail}, live=True)

    assert gmail.calls == [(real_to, "Your custom piece", "Hi")]  # live, clean subject
    assert out.status == "sent"
    assert getattr(out, "mode", None) == "live"


# ── CHANGE 2: atomic exactly-once CLAIM (real Postgres) ─────────────────────────


def _pg_available() -> bool:
    try:
        import psycopg

        psycopg.connect(_DSN).close()
        return True
    except Exception:
        return False


pytestmark_pg = pytest.mark.skipif(not _pg_available(), reason="Postgres not reachable")


def _seed_pending(dsn: str) -> str:
    from actions.store import ensure_schema, record_pending_action

    ensure_schema(dsn)
    return record_pending_action(
        tenant_id="test_safe_send", decision_id=None, type="outreach",
        channel="gmail", worker="growth", target="lead@example.com",
        draft="Body", subject="Hi", conf=0.9, threshold=0.85,
        esc_kind=None, esc_label=None,
        idempotency_key=f"test_safe_send:gmail:{uuid.uuid4().hex}", dsn=dsn,
    )


def _cleanup(dsn: str, action_id: str) -> None:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM actions WHERE id=%s", (action_id,))


@pytestmark_pg
def test_double_approve_sends_exactly_once_second_claims_zero(monkeypatch):
    """A second approve of one pending action must NOT send again: the atomic claim
    returns 0 rows and the call returns the current row without a send."""
    calls: list[tuple] = []
    lock = threading.Lock()

    def fake_send(self, to, subject, body, *, from_addr=None):
        with lock:
            calls.append((to, subject, body))
        return GmailSendResult(message_id="m_mock", deep_link="dl_mock")

    # MOCK the real connector's send — no token exchange, no Google, no real email.
    monkeypatch.setattr(GmailConnector, "send", fake_send)
    # A redirect target so the send actually fires (fail-closed refuses a
    # no-redirect, non-live send); this test is about the atomic exactly-once
    # claim, which is identical for a live or a redirected send.
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "qa-inbox@example.com")

    action_id = _seed_pending(_DSN)
    try:
        first = approve_and_publish(action_id, dsn=_DSN)
        second = approve_and_publish(action_id, dsn=_DSN)  # retry / second approve

        assert first.status == "sent"
        assert first.deep_link == "dl_mock"
        # Second approve found a non-pending row: claim matched 0 rows -> no resend.
        assert second.status == "sent"
        assert len(calls) == 1, f"send fired {len(calls)} times, must be exactly once"
    finally:
        _cleanup(_DSN, action_id)


@pytestmark_pg
def test_concurrent_approve_sends_exactly_once(monkeypatch):
    """Two threads approve the same pending action at once. The conditional UPDATE
    pending->'sending' serializes them in the DB: exactly one wins and sends."""
    calls: list[tuple] = []
    lock = threading.Lock()

    def fake_send(self, to, subject, body, *, from_addr=None):
        with lock:
            calls.append((to, subject, body))
        return GmailSendResult(message_id="m_mock", deep_link="dl_mock")

    monkeypatch.setattr(GmailConnector, "send", fake_send)
    # A redirect target so the send actually fires (fail-closed refuses a
    # no-redirect, non-live send); this test is about the atomic exactly-once
    # claim, which is identical for a live or a redirected send.
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "qa-inbox@example.com")

    action_id = _seed_pending(_DSN)
    results: list[ActionRow] = []
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()  # maximize the race on the claim
        results.append(approve_and_publish(action_id, dsn=_DSN))

    try:
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(calls) == 1, f"send fired {len(calls)} times, must be exactly once"
        # The loser may snapshot the row mid-send ('sending'); neither thread errors,
        # and the settled DB row is 'sent'.
        assert all(r.status in ("sent", "sending") for r in results)
        from actions.store import get_action as _get

        assert _get(action_id, dsn=_DSN).status == "sent"
    finally:
        _cleanup(_DSN, action_id)
