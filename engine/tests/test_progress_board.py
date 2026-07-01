"""Durable ProgressBoard + progress-aware replan — pure/offline tests (no DB).

Proves the board is computed from REAL rows (drafts/analysts/researchers/actions), the
shared run-resolution matches ``build_progress_context``'s in-flight handling, and the
replan trigger fires ONLY on a real, measured contradiction (majority of analyzed leads)
— never a decorative note. Rows are plain stand-ins so the counting is proven without PG.
"""

from __future__ import annotations

from types import SimpleNamespace

from studio.agui import CampaignPlan
from studio.campaign_blueprint import build_blueprint
from studio.progress_board import (
    ProgressBoard,
    compute_board,
    detect_contradiction,
    dominant_measured_objection,
    resolve_active_run,
)

_RUN = "team-camp_abc-def"


def _analyst(objection: str | None, signal: str = "stated") -> dict:
    out = {"primary_objection": objection or "none-found", "objection_signal": signal}
    return {"role": "analyst", "output": out}


def _draft(channel: str) -> dict:
    return {"role": "draft", "input": {"channel": channel}, "output": {"hook": "x"}}


def _action(run_id: str, status: str) -> SimpleNamespace:
    return SimpleNamespace(run_id=run_id, status=status)


def test_resolve_active_run_prefers_in_flight_action_run() -> None:
    # No runs row yet, an action points at an in-flight run -> surface it, record=None.
    run_id, record = resolve_active_run([], [_action("team-inflight", "pending")])
    assert run_id == "team-inflight" and record is None
    # A materialized run is authoritative.
    rec = SimpleNamespace(run_id=_RUN)
    run_id2, record2 = resolve_active_run([rec], [_action(_RUN, "pending")])
    assert run_id2 == _RUN and record2 is rec


def test_compute_board_counts_real_rows() -> None:
    agent_runs = [
        {"role": "researcher", "output": {"cited": 2, "sources": [{}], "degraded": False}},
        {"role": "researcher", "output": {"cited": 0, "sources": [], "degraded": True}},
        _analyst("price"),
        _analyst("price"),
        _analyst(None, signal="insufficient-signal"),
        _draft("sms"),
        _draft("email"),
    ]
    run_actions = [_action(_RUN, "pending"), _action(_RUN, "pending")]
    plan = CampaignPlan(goal="g", channels=["sms", "email"], output_count=2)
    board = compute_board(_RUN, None, agent_runs, run_actions, plan)

    assert isinstance(board, ProgressBoard)
    assert board.run_id == _RUN
    assert board.leads_done == 2  # two draft rows
    assert board.objections_resolved == ["price", "price"]  # grounded only
    # One degraded research + one insufficient-objection lead surface as MISSING (honest).
    assert any("degraded" in m for m in board.missing)
    assert any("insufficient signal" in m for m in board.missing)
    # quota is 1 per channel (2 across sms+email); one draft each -> both complete.
    assert set(board.channels_complete) == {"sms", "email"}


def test_compute_board_honest_empty_when_no_run() -> None:
    board = compute_board(None, None, [], [], CampaignPlan())
    assert board.run_status == "none"
    assert board.leads_done == 0 and board.objections_resolved == []
    assert board.known == []


def test_detect_contradiction_only_on_real_measured_mismatch() -> None:
    # Blueprint assumes price (reactivation cohort). Analysts measure trust in a majority.
    bp = build_blueprint(
        CampaignPlan(goal="g", target_category="past-customer-reactivation", output_count=3),
        "t", None, use_llm=False,
    )
    assert bp.assumed_dominant_objection == "price"
    trust_majority = [_analyst("trust"), _analyst("trust"), _analyst("price")]
    contradiction = detect_contradiction(bp, trust_majority)
    assert contradiction is not None and "trust" in contradiction
    assert dominant_measured_objection(trust_majority) == "trust"

    # No contradiction when the measured majority matches the assumption.
    price_majority = [_analyst("price"), _analyst("price"), _analyst("trust")]
    assert detect_contradiction(bp, price_majority) is None
    # A single outlier is NOT a contradiction (needs a strict majority).
    one_outlier = [_analyst("price"), _analyst("trust")]
    assert detect_contradiction(bp, one_outlier) is None
