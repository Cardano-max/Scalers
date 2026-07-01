"""Offline unit test for the studio chat store (P2 interactive Slice 1).

Exercises the in-memory ``ChatStore`` only — no DB, no model, no network — so it
runs in the standard hermetic suite. The Postgres path shares the same protocol
and is exercised live in the verify step.
"""

from __future__ import annotations

import pytest

from studio.chat_store import InMemoryChatStore, VALID_ROLES


def test_append_and_history_orders_by_seq() -> None:
    store = InMemoryChatStore()
    op = store.append_turn("s1", "operator", "hello")
    host = store.append_turn("s1", "host", "hi back", model="anthropic:claude-sonnet-4-6")

    assert op.seq == 1 and op.role == "operator" and op.model is None
    assert host.seq == 2 and host.role == "host"
    # host turns carry the real model pin; operator turns do not.
    assert host.model == "anthropic:claude-sonnet-4-6"

    hist = store.history("s1")
    assert [t.text for t in hist] == ["hello", "hi back"]
    assert [t.seq for t in hist] == [1, 2]


def test_sessions_are_isolated() -> None:
    store = InMemoryChatStore()
    store.append_turn("a", "operator", "a1")
    store.append_turn("b", "operator", "b1")
    assert [t.text for t in store.history("a")] == ["a1"]
    assert store.history("a")[0].seq == 1
    assert store.history("b")[0].seq == 1


def test_rejects_unknown_role() -> None:
    store = InMemoryChatStore()
    with pytest.raises(ValueError):
        store.append_turn("s1", "marketer", "nope")
    # P2 shipped (operator, host); P3.1 added the labeled brainstorm role cells;
    # P3.x added the wired traced-run roles surfaced from a run_campaign, plus the
    # 'thinking' role that persists the Host's REAL extended-thinking trace. P1 (tattoo
    # pivot) added the per-lead 'analyst' (customer-psychology) role.
    assert set(VALID_ROLES) == {
        "operator", "host", "funnel_architect", "copywriter", "critic", "jury",
        "researcher", "strategist", "draft", "thinking", "analyst",
    }
