"""Draft-count reconciliation — CustomerAcq-sgr (extends the P2-D output ledger, 65w.8).

The operator must see requested / expected / created / in-review-queue / skipped /
failed with the EXACT per-row reasons, and the number the UI panel shows must equal the
number ``campaign_state`` reports (so voice can never say 2 when the UI shows 10).

Credit-INDEPENDENT: ``build_campaign_state`` / ``build_reconciliation`` are pure over the
seeded action rows + the persisted output ledger — no model, no key, no network.
"""

from __future__ import annotations

from studio.campaign_state import build_campaign_state, build_reconciliation

RUN = "team-camp_recon-1"


def _action(idx: int, cust: str, name: str, *, status: str = "pending") -> dict:
    return {
        "id": f"act_{cust}",
        "run_id": RUN,
        "target": name,
        "subject": f"Hi {name}",
        "draft": "body",
        "channel": "gmail",
        "status": status,
        "idempotency_key": f"{RUN}:{cust}",
        "context": "{}",
        "created_at": f"2026-07-01T10:00:{idx:02d}+00:00",
    }


def _jury_with_ledger(ledger: dict) -> dict:
    return {"role": "jury", "model": "m", "input": {}, "output": {"decision": "review", "output_ledger": ledger}}


def test_twelve_requested_ten_created_two_skipped_reconciles():
    # 12 requested -> 10 created + 2 skipped (with reasons) = fully accounted.
    actions = [_action(i, f"c{i}", f"Lead {i}") for i in range(1, 11)]  # 10 created
    ledger = {
        "expected": 12,
        "drafted": 10,
        "skipped": [
            {"row": 3, "lead": "c-x", "reason": "no contact method (no email, phone, handle, or name)"},
            {"row": 7, "lead": "c-y", "reason": "not found in database (row did not match a customer)"},
        ],
        "reconciled": True,
    }
    state = build_campaign_state(run_id=RUN, action_rows=actions, agent_runs=[_jury_with_ledger(ledger)])
    r = state["reconciliation"]

    assert r["requested"] == 12
    assert r["expected"] == 12
    assert r["created"] == 10
    assert r["inQueue"] == 10  # all pending
    assert len(r["skipped"]) == 2
    assert r["failed"] == []
    assert r["accounted"] == 12
    assert r["reconciled"] is True
    # Per-row reasons are carried verbatim for the panel.
    assert any("no contact method" in s["reason"] for s in r["skipped"])
    assert {s["row"] for s in r["skipped"]} == {3, 7}


def test_panel_and_campaign_state_agree_on_the_count():
    # The panel renders `reconciliation` verbatim; the number it shows MUST equal the
    # count campaign_state reports (voice reads the same state) — never 2 vs 10.
    actions = [_action(i, f"c{i}", f"Lead {i}") for i in range(1, 11)]
    ledger = {"expected": 12, "drafted": 10, "skipped": [{"row": 1, "lead": "a", "reason": "beyond output cap of 10"}, {"row": 2, "lead": "b", "reason": "cohort short"}], "reconciled": True}
    state = build_campaign_state(run_id=RUN, action_rows=actions, agent_runs=[_jury_with_ledger(ledger)])

    assert state["counts"]["drafts"] == state["reconciliation"]["created"] == 10
    assert state["expected"] == state["reconciliation"]["expected"] == 12
    # The exact object the endpoint sends the panel is what campaign_state computed.
    assert build_reconciliation(created=10, counts=state["counts"], ledger=ledger) == state["reconciliation"]


def test_draft_generation_failure_is_counted_as_failed_not_skipped():
    actions = [_action(i, f"c{i}", f"Lead {i}") for i in range(1, 10)]  # 9 created
    ledger = {
        "expected": 10,
        "drafted": 9,
        "skipped": [{"row": 5, "lead": "c-z", "reason": "draft generation failed: ModelHTTPError"}],
        "reconciled": True,
    }
    state = build_campaign_state(run_id=RUN, action_rows=actions, agent_runs=[_jury_with_ledger(ledger)])
    r = state["reconciliation"]
    assert r["created"] == 9
    assert r["skipped"] == []               # the failure is NOT a benign skip
    assert len(r["failed"]) == 1
    assert "draft generation failed" in r["failed"][0]["reason"]
    assert r["accounted"] == 10 and r["reconciled"] is True


def test_undercount_without_reasons_does_not_falsely_reconcile():
    # 12 requested, 10 created, and the ledger has NO skip rows -> 2 unaccounted.
    actions = [_action(i, f"c{i}", f"Lead {i}") for i in range(1, 11)]
    ledger = {"expected": 12, "drafted": 10, "skipped": [], "reconciled": False}
    state = build_campaign_state(run_id=RUN, action_rows=actions, agent_runs=[_jury_with_ledger(ledger)])
    r = state["reconciliation"]
    assert r["created"] == 10 and r["accounted"] == 10 and r["expected"] == 12
    assert r["reconciled"] is False  # honest — 2 rows are unexplained, not hidden


def test_no_ledger_legacy_run_accounts_for_what_ran():
    actions = [_action(i, f"c{i}", f"Lead {i}") for i in range(1, 4)]  # 3 created, no ledger
    state = build_campaign_state(run_id=RUN, action_rows=actions, agent_runs=[])
    r = state["reconciliation"]
    assert r["created"] == 3 and r["expected"] == 3 and r["reconciled"] is True
