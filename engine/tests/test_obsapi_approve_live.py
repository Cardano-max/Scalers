"""The per-draft Review-Queue approve path threads the operator's live authorization
and surfaces the resolved send mode (#11).

The single-draft approve (the GraphQL ``approveAction`` mutation -> ``repo.approve_action``
-> ``actions.publish.approve_and_publish``) is the path the operator's "I approved a real
email and it had [TEST]" complaint lives on. These hermetic checks (no DB, no network)
pin that:

1. the GraphQL contract exposes ``mode`` on ``Action`` and a ``live`` arg on the
   ``approveAction`` mutation, and
2. ``repo.approve_action`` passes ``live`` straight through to the send and surfaces the
   REAL resolved mode it came back with — default False = safe redirect.
"""

from __future__ import annotations

import re

import actions.publish as publish
from actions.store import ActionRow
from obsapi import repo
from obsapi.schema import schema


def _type_block(sdl: str, name: str) -> str:
    m = re.search(r"type %s \{(.*?)\n\}" % re.escape(name), sdl, re.S)
    assert m, f"{name} type missing from SDL"
    return m.group(1)


def test_action_exposes_mode_and_approve_mutation_takes_live():
    sdl = schema.as_str()
    assert "mode" in _type_block(sdl, "Action"), "Action.mode not exposed"
    m = re.search(r"approveAction\((.*?)\)", sdl, re.S)
    assert m, "approveAction mutation missing from SDL"
    assert "live" in m.group(1).lower(), "approveAction does not accept a live arg"


def _sent_row(action_id: str) -> ActionRow:
    return ActionRow(
        id=action_id, tenant_id="ladies8391", type="outreach", channel="gmail",
        draft="Hi", status="sent", target="lead@real.com", subject="Hello",
    )


def test_approve_action_threads_live_true_and_surfaces_live_mode(monkeypatch):
    seen: dict[str, object] = {}

    def fake_publish(action_id, *, connectors=None, dsn=None, live=False):
        seen["action_id"] = action_id
        seen["live"] = live
        row = _sent_row(action_id)
        row.mode = "live" if live else "test_redirect"
        return row

    monkeypatch.setattr(publish, "approve_and_publish", fake_publish)
    # repo.action re-reads the row for the response shape; stub it DB-free.
    monkeypatch.setattr(repo, "action", lambda action_id, tenant_id=None: repo._build_action(None, {
        "id": action_id, "tenant_id": "ladies8391", "type": "outreach", "channel": "gmail",
        "worker": "studio_real_send", "target": "lead@real.com", "created_at": None,
        "subject": "Hello", "context": None, "draft": "Hi", "conf": None, "threshold": None,
        "esc_kind": None, "esc_label": None, "idempotency_key": "k", "status": "sent",
        "recommend": None, "is_seeded": False, "last_error": None,
    }))

    out = repo.approve_action("act_1", "idem_1", live=True)
    assert seen["live"] is True  # the explicit authorization threaded through
    assert out is not None and out.mode == "live"  # real resolved mode surfaced


def test_approve_action_defaults_to_redirect_mode(monkeypatch):
    seen: dict[str, object] = {}

    def fake_publish(action_id, *, connectors=None, dsn=None, live=False):
        seen["live"] = live
        row = _sent_row(action_id)
        row.mode = "live" if live else "test_redirect"
        return row

    monkeypatch.setattr(publish, "approve_and_publish", fake_publish)
    monkeypatch.setattr(repo, "action", lambda action_id, tenant_id=None: repo._build_action(None, {
        "id": action_id, "tenant_id": "ladies8391", "type": "outreach", "channel": "gmail",
        "worker": "team", "target": "lead@real.com", "created_at": None,
        "subject": "Hello", "context": None, "draft": "Hi", "conf": None, "threshold": None,
        "esc_kind": None, "esc_label": None, "idempotency_key": "k", "status": "sent",
        "recommend": None, "is_seeded": False, "last_error": None,
    }))

    out = repo.approve_action("act_1", "idem_1")  # no live arg -> safe default
    assert seen["live"] is False
    assert out is not None and out.mode == "test_redirect"
