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

    result = approve_and_publish("act_gate1", connectors={"gmail": ok}, dsn=None)
    assert len(ok.calls) == 1
    assert result.status == "sent"


class _OkGmail:
    def __init__(self):
        self.calls = []

    def send(self, to, subject, body, *, from_addr=None):
        self.calls.append((to, subject, body))
        from connectors.gmail import GmailSendResult

        return GmailSendResult(message_id="m1", deep_link="dl")


def test_unknown_tenant_no_row_is_refused_fail_closed(monkeypatch):
    # wwy.4: a tenant with NO registry row is NOT silent passthrough anymore —
    # without an explicit legacy allowlist entry it is REFUSED. (Was the
    # fail-open hole: a typo / fresh-DB tenant went live.)
    monkeypatch.delenv("TEST_MODE_LEGACY_PASSTHROUGH", raising=False)
    store = _FakeStore(_action("ladies8391"))
    ok = _OkGmail()
    _wire(monkeypatch, store, None)  # no tenants row

    with pytest.raises(TestModeSendBlockedError):
        approve_and_publish("act_gate1", connectors={"gmail": ok}, dsn=None)
    assert ok.calls == []
    assert store.rows["act_gate1"].status == "pending"


def test_legacy_passthrough_only_via_explicit_env_allowlist(monkeypatch):
    # AC(d): ladies8391 stays passthrough — but ONLY because it is explicitly
    # listed in TEST_MODE_LEGACY_PASSTHROUGH.
    monkeypatch.setenv("TEST_MODE_LEGACY_PASSTHROUGH", "ladies8391")
    monkeypatch.setenv("GMAIL_REDIRECT_TO", "qa-inbox@example.com")  # deterministic send
    store = _FakeStore(_action("ladies8391"))
    ok = _OkGmail()
    _wire(monkeypatch, store, None)  # no tenants row

    result = approve_and_publish("act_gate1", connectors={"gmail": ok}, dsn=None)
    assert len(ok.calls) == 1
    assert result.status == "sent"


def test_registry_read_error_refuses_fail_closed(monkeypatch):
    # AC(a): a transient registry read error must REFUSE, never silently pass.
    import psycopg

    store = _FakeStore(_action("skindesign"))
    ok = _OkGmail()
    monkeypatch.setattr(publish, "get_action", store.get_action)
    monkeypatch.setattr(publish, "update_status", store.update_status)
    monkeypatch.setattr(publish, "claim_for_send", store.claim_for_send)
    import tenants.store as tstore

    def _boom(tid, dsn=None):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(tstore, "get_tenant", _boom)

    with pytest.raises(TestModeSendBlockedError) as ei:
        approve_and_publish("act_gate1", connectors={"gmail": ok}, dsn=None)
    assert "fail closed" in str(ei.value).lower()
    assert ok.calls == []
    assert store.rows["act_gate1"].status == "pending"


def test_protected_tenant_missing_row_is_refused(monkeypatch):
    # AC(b): skindesign (PROTECTED) with no registry row is REFUSED even though
    # the env allowlist would clear an UNPROTECTED no-row tenant.
    monkeypatch.setenv("TEST_MODE_LEGACY_PASSTHROUGH", "skindesign")  # must NOT help a protected tenant
    store = _FakeStore(_action("skindesign"))
    ok = _OkGmail()
    _wire(monkeypatch, store, None)  # no registry row

    with pytest.raises(TestModeSendBlockedError) as ei:
        approve_and_publish("act_gate1", connectors={"gmail": ok}, dsn=None)
    assert "protected" in str(ei.value).lower()
    assert ok.calls == []


def test_fresh_db_no_table_protected_refused_after_ensure_retry(monkeypatch):
    # AC(c): a fresh DB without the tenants table (UndefinedTable) -> ensure_schema
    # + retry -> still no row -> skindesign PROTECTED -> refused. The missing
    # table can never fall through to a live send.
    import psycopg

    store = _FakeStore(_action("skindesign"))
    ok = _OkGmail()
    monkeypatch.setattr(publish, "get_action", store.get_action)
    monkeypatch.setattr(publish, "update_status", store.update_status)
    monkeypatch.setattr(publish, "claim_for_send", store.claim_for_send)
    import tenants.store as tstore

    calls = {"n": 0}

    def _fake_get_tenant(tid, dsn=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise psycopg.errors.UndefinedTable('relation "tenants" does not exist')
        return None  # after ensure_schema: table exists, still no row

    monkeypatch.setattr(tstore, "get_tenant", _fake_get_tenant)
    monkeypatch.setattr(tstore, "ensure_schema", lambda dsn=None: None)

    with pytest.raises(TestModeSendBlockedError):
        approve_and_publish("act_gate1", connectors={"gmail": ok}, dsn=None)
    assert calls["n"] == 2  # raised once, retried once after ensure_schema
    assert ok.calls == []


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
