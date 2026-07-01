"""Structured ProgressBoard + hard-gated replan — pure/offline tests (no DB).

Proves the board is computed from REAL rows (drafts/analysts/researchers/actions), the
shared run-resolution matches ``build_progress_context``'s in-flight handling, and
``maybe_replan`` returns a CONCRETE PlanDelta ONLY under all gates (sample/margin/cap/
mismatch) — never a decorative no-diff note. Rows are plain stand-ins so the counting is
proven without PG.
"""

from __future__ import annotations

from types import SimpleNamespace

from studio.agui import CampaignPlan
from studio.campaign_blueprint import build_blueprint
from studio.progress_board import (
    PlanDelta,
    ProgressBoard,
    REPLAN_CAP,
    compute_board,
    dominant_measured_objection,
    maybe_replan,
    replan_event_id,
    resolve_active_run,
)

_RUN = "team-camp_abc-def"


def _analyst(objection: str | None, cust: str, signal: str = "stated") -> dict:
    out = {"primary_objection": objection or "none-found", "objection_signal": signal}
    return {"role": "analyst", "input": {"customer_id": cust}, "output": out}


def _draft(channel: str, cust: str) -> dict:
    return {"role": "draft", "input": {"channel": channel, "customer_id": cust},
            "output": {"hook": "x"}}


def _action(run_id: str, status: str) -> SimpleNamespace:
    return SimpleNamespace(run_id=run_id, status=status)


def test_resolve_active_run_prefers_in_flight_action_run() -> None:
    run_id, record = resolve_active_run([], [_action("team-inflight", "pending")])
    assert run_id == "team-inflight" and record is None
    rec = SimpleNamespace(run_id=_RUN)
    run_id2, record2 = resolve_active_run([rec], [_action(_RUN, "pending")])
    assert run_id2 == _RUN and record2 is rec


def test_compute_board_counts_real_rows_and_addressed_needs_a_staged_draft() -> None:
    agent_runs = [
        {"role": "researcher", "output": {"cited": 2, "sources": [{}], "degraded": False}},
        {"role": "researcher", "output": {"cited": 0, "sources": [], "degraded": True}},
        _analyst("price", "c1"),
        _analyst("price", "c2"),
        _analyst(None, "c3", signal="insufficient-signal"),
        _draft("sms", "c1"),
        _draft("email", "c2"),
    ]
    run_actions = [_action(_RUN, "pending"), _action(_RUN, "pending")]
    plan = CampaignPlan(goal="g", channels=["sms", "email"], output_count=2)
    board = compute_board(_RUN, None, agent_runs, run_actions, plan)

    assert isinstance(board, ProgressBoard)
    assert board.leads_done == 2
    # objections_ADDRESSED: only c1/c2 (they each produced a staged draft); c3 had no draft.
    assert board.objections_addressed == ["price", "price"]
    assert any("degraded" in m for m in board.missing)
    assert any("insufficient signal" in m for m in board.missing)
    assert set(board.channels_complete) == {"sms", "email"}


def test_addressed_excludes_a_measured_objection_with_no_staged_draft() -> None:
    # An analyst measured 'trust' for c9 but NO draft was staged for c9 -> not addressed.
    agent_runs = [_analyst("trust", "c9"), _analyst("price", "c1"), _draft("sms", "c1")]
    board = compute_board(_RUN, None, agent_runs, [], CampaignPlan(output_count=2))
    assert board.objections_addressed == ["price"]


def test_compute_board_honest_empty_when_no_run() -> None:
    board = compute_board(None, None, [], [], CampaignPlan())
    assert board.run_status == "none"
    assert board.leads_done == 0 and board.objections_addressed == []


def test_maybe_replan_only_on_a_gated_measured_mismatch_and_emits_a_concrete_delta() -> None:
    from studio.progress_board import MIN_SAMPLE

    assert MIN_SAMPLE >= 3  # a single noisy read must never flip the plan
    bp = build_blueprint(
        CampaignPlan(goal="g", target_category="past-customer-reactivation", output_count=4),
        "t", None, use_llm=False,
    )
    assert bp.assumed_dominant_objection == "price"

    # Trust beats the assumption (price) across >= MIN_SAMPLE reads with a clear margin -> a delta.
    trust_majority = [
        _analyst("trust", "a"), _analyst("trust", "b"), _analyst("trust", "c"), _analyst("price", "d")
    ]
    delta = maybe_replan(bp, trust_majority, replans_so_far=0)
    assert isinstance(delta, PlanDelta)
    assert delta.from_objection == "price" and delta.to_objection == "trust"
    assert delta.from_objection != delta.to_objection  # non-empty by construction
    assert delta.reason and "trust" in delta.reason
    assert dominant_measured_objection(trust_majority) == "trust"

    # A SINGLE noisy analyst read must NOT flip the plan (below MIN_SAMPLE).
    assert maybe_replan(bp, [_analyst("trust", "a")]) is None
    # Two reads is still below MIN_SAMPLE=3 -> no replan (even a unanimous 'trust').
    assert maybe_replan(bp, [_analyst("trust", "a"), _analyst("trust", "b")]) is None
    # No delta when the measured majority MATCHES the assumption.
    assert maybe_replan(bp, [_analyst("price", "a"), _analyst("price", "b"), _analyst("price", "c")]) is None
    # No delta at/over the cap (no thrash).
    assert maybe_replan(bp, trust_majority, replans_so_far=REPLAN_CAP) is None


def test_replan_event_id_is_deterministic() -> None:
    a = replan_event_id(_RUN, "price", "trust", 3)
    b = replan_event_id(_RUN, "price", "trust", 3)
    c = replan_event_id(_RUN, "price", "payment", 3)
    assert a == b and a != c and a.startswith("ar_replan_")
