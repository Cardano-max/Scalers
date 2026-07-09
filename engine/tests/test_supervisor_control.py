"""Supervisor full-duplex control: directives + plan conformance (spec: the
supervisor orchestrates — plans, peeks, corrects — not just watches)."""

from __future__ import annotations

import os
import uuid

import pytest

from studio.supervisor_control import (
    VALID_KINDS,
    apply_directives,
    check_plan_conformance,
    issue_directive,
    list_directives,
)

_pg = pytest.mark.skipif(
    not os.environ.get("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)


class _Plan:
    deep_research = True
    research_depth = ""
    personalize = True
    per_lead = True
    attach_artwork = False


# ── pure: plan conformance ─────────────────────────────────────────────────── #


def test_conformance_flags_empty_research_when_plan_orders_deep():
    runs = [
        {"role": "researcher", "input": {"customer_id": "c1"}, "output": {"sources": []}},
        {"role": "researcher", "input": {"customer_id": "c2"}, "output": {}},
    ]
    fired: set[str] = set()
    found = check_plan_conformance(_Plan(), runs, fired_rules=fired)
    assert any(f["rule"] == "research-missing" for f in found)
    # Fires ONCE per run — the second sweep is silent.
    assert not check_plan_conformance(_Plan(), runs, fired_rules=fired)


def test_conformance_flags_draft_without_analyst_step():
    runs = [
        {"role": "analyst", "input": {"customer_id": "c1"}, "output": {}},
        {"role": "draft", "input": {"customer_id": "c1"}, "output": {}},
        {"role": "draft", "input": {"customer_id": "c2"}, "output": {}},  # no analyst!
    ]
    found = check_plan_conformance(_Plan(), runs, fired_rules=set())
    rules = {f["rule"] for f in found}
    assert "analysis-missing" in rules


def test_conformance_clean_run_returns_nothing():
    runs = [
        {"role": "researcher", "input": {"customer_id": "c1"}, "output": {"sources": [{"url": "https://x"}]}},
        {"role": "analyst", "input": {"customer_id": "c1"}, "output": {}},
        {"role": "draft", "input": {"customer_id": "c1"}, "output": {}},
    ]
    assert check_plan_conformance(_Plan(), runs, fired_rules=set()) == []


# ── DB: directive lifecycle ────────────────────────────────────────────────── #


@pytest.mark.integration
@_pg
def test_directive_roundtrip_and_apply():
    run_id = "run_test_" + uuid.uuid4().hex[:8]
    issue_directive(run_id, "t_test", "set_angle", {"angle": "answer the price objection"})
    issue_directive(run_id, "t_test", "skip_lead", {"customer_id": "cust_x"})
    issue_directive(run_id, "t_test", "guide_copy", {"text": "mention flexible scheduling"})
    issue_directive(run_id, "t_test", "redo_lead", {"customer_id": "cust_y"})

    recorded: list[dict] = []
    changes = apply_directives(
        run_id, "t_test",
        record_agent_run=lambda **kw: recorded.append(kw),
    )
    assert changes["angle"] == "answer the price objection"
    assert changes["skip_customer_ids"] == {"cust_x"}
    assert changes["redo_customer_ids"] == {"cust_y"}
    assert changes["guidance"] == ["mention flexible scheduling"]
    assert not changes["abort"] and not changes["pause"]
    # Every application is a visible supervisor step.
    assert len(recorded) == 4 and all(kw["role"] == "supervisor" for kw in recorded)
    # Consumed exactly once: a second sweep is a no-op.
    again = apply_directives(run_id, "t_test")
    assert again["applied"] == []
    # The ledger keeps the audit trail.
    rows = list_directives(run_id)
    assert len(rows) == 4 and all(r["status"] == "applied" for r in rows)


@pytest.mark.integration
@_pg
def test_unknown_directive_kind_refused():
    with pytest.raises(ValueError):
        issue_directive("run_x", "t_test", "arm_live_sends", {})
    assert "arm_live_sends" not in VALID_KINDS
