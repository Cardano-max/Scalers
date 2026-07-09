"""Mock/sandbox execution channel for the tlv.6 demo slice.

Proves: an approved 'demo'-channel action DELIVERS to the sandbox (status sent +
'Delivered (sandbox)' + a synthetic deep_link) with NO external provider call;
exactly-once holds; and the sandbox exemption does NOT weaken the real-send
test-mode gate — a real (gmail) send to a blocked recipient still raises while the
demo channel to the same recipient delivers. DB-free (in-memory store fake).
"""

from __future__ import annotations

import pytest

import actions.publish as publish
from actions.publish import approve_and_publish
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
        import datetime as _dt

        row = self.rows.get(action_id)
        if row is None or row.status != "pending":
            return None
        row.status = "sending"
        row.autonomy = "approved"
        row.approved_at = _dt.datetime.now(_dt.timezone.utc)
        return row


@pytest.fixture
def patched_store(monkeypatch):
    def _install(*rows: ActionRow) -> _FakeStore:
        store = _FakeStore(*rows)
        monkeypatch.setattr(publish, "get_action", store.get_action)
        monkeypatch.setattr(publish, "update_status", store.update_status)
        monkeypatch.setattr(publish, "claim_for_send", store.claim_for_send)
        return store

    return _install


def _pending(channel="demo", **kw) -> ActionRow:
    kw.setdefault("id", "act_demo1")
    kw.setdefault("tenant_id", "demo_studio")
    kw.setdefault("type", "outreach")
    kw.setdefault("draft", "Hi Ana — a fresh idea for your next piece.")
    kw.setdefault("status", "pending")
    kw.setdefault("target", "ana@example.com")
    kw.setdefault("subject", "An idea for you")
    kw.setdefault("idempotency_key", "demo_studio:demo:ana@example.com:1")
    return ActionRow(channel=channel, **kw)


class _FakeDemo:
    is_mock = True

    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []

    def deliver(self, *, to, subject, body):
        from connectors.demo import DeliveryReceipt
        from datetime import datetime, timezone

        self.calls.append((to, subject, body))
        return DeliveryReceipt(deep_link=f"sandbox://demo/{len(self.calls)}",
                               delivered_at=datetime.now(timezone.utc))


def test_demo_channel_delivers_to_sandbox_with_no_provider(patched_store):
    patched_store(_pending())
    out = approve_and_publish("act_demo1", connectors={})  # default DemoConnector

    assert out.status == "sent"
    assert out.outcome_label == "Delivered (sandbox)"
    assert out.outcome_kind == "success"
    assert out.deep_link.startswith("sandbox://demo/")
    assert out.autonomy == "approved"
    assert out.approved_at is not None and out.sent_at is not None
    assert getattr(out, "mode", None) == "sandbox"


def test_demo_exactly_once_does_not_redeliver(patched_store):
    patched_store(_pending())
    demo = _FakeDemo()
    first = approve_and_publish("act_demo1", connectors={"demo": demo})
    assert first.status == "sent"
    second = approve_and_publish("act_demo1", connectors={"demo": demo})  # replay

    assert second.status == "sent"
    assert len(demo.calls) == 1  # sandbox delivery happened exactly once


def test_demo_default_connector_is_a_mock(patched_store):
    from connectors.demo import DemoConnector

    assert DemoConnector().is_mock is True  # honest: never a real send
    patched_store(_pending())
    out = approve_and_publish("act_demo1", connectors={})
    assert out.status == "sent"  # a mock delivers fine on the demo path (no _ensure_real)


def test_sandbox_exemption_does_not_weaken_the_real_send_gate(patched_store, monkeypatch):
    """The demo channel skips the test-mode gate (nothing to protect), but a REAL
    channel to the same blocked recipient is STILL refused — the gate is intact."""
    def _blocked(tenant_id, recipient, dsn=None):
        return False, "test-mode: recipient not on allowlist"

    monkeypatch.setattr("tenants.store.check_send_allowed", _blocked)

    # Real channel -> gate refuses.
    patched_store(_pending(id="act_gmail1", channel="gmail"))
    with pytest.raises(publish.TestModeSendBlockedError):
        approve_and_publish("act_gmail1", connectors={}, live=True)

    # Demo channel to the SAME recipient -> delivers to the sandbox (exempt).
    patched_store(_pending(id="act_demo2"))
    out = approve_and_publish("act_demo2", connectors={})
    assert out.status == "sent" and out.outcome_label == "Delivered (sandbox)"
