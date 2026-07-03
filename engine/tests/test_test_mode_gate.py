"""Server-side TEST-MODE send gate — CustomerAcq-ju1.1.

The skindesign tenant holds 1,093 REAL customers. One accidental live send is a
client catastrophe, so the gate is safe-by-construction and SERVER-SIDE: when
``tenants.test_mode`` is true, ANY send attempt for that tenant is refused with an
explicit "TEST MODE" error BEFORE connector construction/dispatch — regardless of
redirect config (GMAIL_REDIRECT_TO) or the operator ``live`` toggle. Test sends are
allowed ONLY to an explicit operator-approved allowlist (empty by default).

Every real send funnels through ``actions.publish.approve_and_publish`` (campaign
send + override + engagement all delegate to it), so the gate lives there — above
the exactly-once claim, so a refused action stays PENDING (re-approvable after the
tenant is un-held) and no connector is ever built.

Unit lane: store + tenants faked in-memory (mirrors test_actions_publish.py).
Integration lane: the tenants table round-trips on real PG.
"""

from __future__ import annotations

import datetime as _dt

import pytest

import actions.publish as publish
from actions.publish import TestModeSendBlockedError, approve_and_publish
from actions.store import ActionRow


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
        row = self.rows.get(action_id)
        if row is None or row.status != "pending":
            return None
        row.status = "sending"
        row.autonomy = "approved"
        row.approved_at = _dt.datetime.now(_dt.timezone.utc)
        return row


class _FakeGmail:
    """Boundary assert: any call = a connector dispatch happened."""

    def __init__(self):
        self.calls: list[tuple] = []

    def send(self, to, subject, body, *, from_addr=None):
        self.calls.append((to, subject, body))
        raise AssertionError("connector dispatched for a test-mode tenant")


def _action(tenant: str, target: str = "real.customer@example.com") -> ActionRow:
    return ActionRow(
        id="act_gate1",
        tenant_id=tenant,
        type="outreach",
        channel="gmail",
        draft="Hi there",
        status="pending",
        target=target,
        subject="Hello",
        idempotency_key=f"{tenant}:gate1",
    )


def _wire(monkeypatch, store: _FakeStore, tenant_row: dict | None):
    monkeypatch.setattr(publish, "get_action", store.get_action)
    monkeypatch.setattr(publish, "update_status", store.update_status)
    monkeypatch.setattr(publish, "claim_for_send", store.claim_for_send)
    # Tenant lookup faked at the same seam the gate reads.
    import tenants.store as tstore

    monkeypatch.setattr(tstore, "get_tenant", lambda tid, dsn=None: tenant_row)


SD = {
    "id": "skindesign",
    "name": "Skin Design Tattoo",
    "test_mode": True,
    "test_send_allowlist": [],
}


def test_test_mode_tenant_send_is_refused_before_connector(monkeypatch):
    store = _FakeStore(_action("skindesign"))
    fake = _FakeGmail()
    _wire(monkeypatch, store, SD)

    with pytest.raises(TestModeSendBlockedError) as ei:
        approve_and_publish("act_gate1", connectors={"gmail": fake}, dsn=None)

    assert "TEST MODE" in str(ei.value)
    assert fake.calls == []  # zero connector dispatches
    assert store.rows["act_gate1"].status == "pending"  # not claimed/burned
    assert "TEST MODE" in (store.rows["act_gate1"].last_error or "")


def test_live_toggle_does_not_bypass_the_gate(monkeypatch):
    # The operator's explicit live=True (the strongest UI toggle) must still refuse.
    store = _FakeStore(_action("skindesign"))
    fake = _FakeGmail()
    _wire(monkeypatch, store, SD)

    with pytest.raises(TestModeSendBlockedError):
        approve_and_publish("act_gate1", connectors={"gmail": fake}, dsn=None, live=True)
    assert fake.calls == []


def test_redirect_config_does_not_bypass_the_gate(monkeypatch):
    # Even with a redirect inbox configured (the "safe" path), a test-mode tenant
    # sends NOTHING — the gate fires before any send-mode decision.
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "operator@example.com")
    store = _FakeStore(_action("skindesign"))
    fake = _FakeGmail()
    _wire(monkeypatch, store, SD)

    with pytest.raises(TestModeSendBlockedError):
        approve_and_publish("act_gate1", connectors={"gmail": fake}, dsn=None)
    assert fake.calls == []


def test_gate_covers_every_channel(monkeypatch):
    for channel in ("gmail", "email", "facebook", "instagram"):
        row = _action("skindesign")
        row.channel = channel
        store = _FakeStore(row)
        _wire(monkeypatch, store, SD)
        with pytest.raises(TestModeSendBlockedError):
            approve_and_publish("act_gate1", connectors={}, dsn=None)
        assert store.rows["act_gate1"].status == "pending", channel


def test_allowlisted_recipient_may_receive_a_test_send(monkeypatch):
    # The ONLY exception: an explicit operator-approved allowlist address.
    class _OkGmail:
        def __init__(self):
            self.calls = []

        def send(self, to, subject, body, *, from_addr=None):
            self.calls.append((to, subject, body))
            from connectors.gmail import GmailSendResult

            return GmailSendResult(message_id="m1", deep_link="dl")

    row = _action("skindesign", target="operator@example.com")
    store = _FakeStore(row)
    ok = _OkGmail()
    tenant = {**SD, "test_send_allowlist": ["operator@example.com"]}
    _wire(monkeypatch, store, tenant)
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "qa-inbox@example.com")  # fail-closed needs a redirect target

    result = approve_and_publish("act_gate1", connectors={"gmail": ok}, dsn=None)
    assert len(ok.calls) == 1
    assert result.status == "sent"


def test_unknown_tenant_passes_through_unchanged(monkeypatch):
    # ladies8391 has no tenants row -> legacy behavior untouched.
    class _OkGmail:
        def __init__(self):
            self.calls = []

        def send(self, to, subject, body, *, from_addr=None):
            self.calls.append((to, subject, body))
            from connectors.gmail import GmailSendResult

            return GmailSendResult(message_id="m1", deep_link="dl")

    store = _FakeStore(_action("ladies8391"))
    ok = _OkGmail()
    _wire(monkeypatch, store, None)  # no tenants row
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "qa-inbox@example.com")  # fail-closed needs a redirect target

    result = approve_and_publish("act_gate1", connectors={"gmail": ok}, dsn=None)
    assert len(ok.calls) == 1
    assert result.status == "sent"


def test_test_mode_false_tenant_passes_through(monkeypatch):
    class _OkGmail:
        def __init__(self):
            self.calls = []

        def send(self, to, subject, body, *, from_addr=None):
            self.calls.append((to, subject, body))
            from connectors.gmail import GmailSendResult

            return GmailSendResult(message_id="m1", deep_link="dl")

    store = _FakeStore(_action("skindesign"))
    ok = _OkGmail()
    _wire(monkeypatch, store, {**SD, "test_mode": False})
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "qa-inbox@example.com")  # fail-closed needs a redirect target

    result = approve_and_publish("act_gate1", connectors={"gmail": ok}, dsn=None)
    assert len(ok.calls) == 1
    assert result.status == "sent"


# ── tenants store round-trip on real PG ─────────────────────────────────────── #


@pytest.mark.integration
def test_tenants_store_roundtrip_on_postgres():
    import os
    import uuid

    import psycopg

    from tenants.store import ensure_schema, get_tenant, upsert_tenant

    dsn = (
        os.environ.get("ENGINE_DATABASE_URL")
        or "postgresql://scalers:scalers@localhost:5432/scalers"
    )
    tid = f"t_test_{uuid.uuid4().hex[:8]}"
    ensure_schema(dsn)
    try:
        upsert_tenant(tid, "Gate Test Tenant", test_mode=True, dsn=dsn)
        row = get_tenant(tid, dsn=dsn)
        assert row is not None and row["test_mode"] is True
        assert row["test_send_allowlist"] == []
        # allowlist update round-trips; test_mode persists server-side
        upsert_tenant(
            tid, "Gate Test Tenant", test_mode=True, allowlist=["op@example.com"], dsn=dsn
        )
        row = get_tenant(tid, dsn=dsn)
        assert row["test_send_allowlist"] == ["op@example.com"]
    finally:
        with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as conn:
            conn.execute("DELETE FROM tenants WHERE id=%s", (tid,))
