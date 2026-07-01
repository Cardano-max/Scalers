"""Real-state campaign query for the voice supervisor — CustomerAcq-6bv / 65w.12.

THE BUG (operator, real run team-camp_fa3aa98cd10a-...): the voice supervisor
answered campaign-state questions by GUESSING — it named a DIFFERENT lead than the
frontend showed for "draft #1" and reported the wrong draft count (2 when 7 existed).
The voice model had no grounded state surface, so it hallucinated.

THE FIX: one credit-INDEPENDENT state-query (``build_campaign_state`` — pure over DB
rows) that both the frontend order and the voice answers read from, so they can never
disagree. Draft #1 is the REAL first lead by creation order (matching the review
queue), counts come from real rows, and "did strategist / critic run" reflects the
real agent_runs INCLUDING the fail-closed ``failed`` status (0dy). No model key needed.
"""

from __future__ import annotations

import pytest

from studio.campaign_state import (
    build_campaign_state,
    describe_draft,
    describe_state,
)

RUN = "team-camp_fa3aa98cd10a-3541ee7c28c2"


def _action(idx: int, cust: str, name: str, *, status: str = "pending", conf=None) -> dict:
    """A staged draft action row (as list_actions_for_run yields, normalized)."""
    return {
        "id": f"act_{cust}",
        "run_id": RUN,
        "target": name,
        "subject": f"We miss you, {name.split()[0]}",
        "draft": f"Hi {name.split()[0]}, come back for a fresh piece.",
        "status": status,
        "conf": conf,
        "idempotency_key": f"{RUN}:{cust}",
        "context": '{"skill_used": "objection-recovery", "skill_why": "stated price objection"}',
        # created_at is pre-ordered by the DB query (oldest first); index encodes order.
        "created_at": f"2026-07-01T17:45:{40 + idx:02d}+00:00",
    }


def _ar(role: str, output: dict, cust: str | None = None, model: str = "m") -> dict:
    inp = {"customer_id": cust} if cust else {}
    return {"role": role, "model": model, "input": inp, "output": output}


def _healthy_runs_and_agents():
    """Three real leads (Sarah, Priya, Dana) with a clean strategist + per-lead critic."""
    actions = [
        _action(1, "cust_fd63", "Sarah Kim"),
        _action(2, "cust_5448", "Priya Anand"),
        _action(3, "cust_96e2", "Dana Ruiz"),
    ]
    agent_runs = [
        _ar("planner", {"ok": True}),
        _ar("strategist", {"target_angle": "warm win-back with a real offer"}),
        _ar("researcher", {"cited": 3}, cust="cust_fd63"),
        _ar("draft", {"hook": "x"}, cust="cust_fd63"),
        _ar("critic", {"verdict": "approve", "confidence": 0.9, "rationale": "on-voice"}, cust="cust_fd63"),
        _ar("researcher", {"cited": 2}, cust="cust_5448"),
        _ar("draft", {"hook": "y"}, cust="cust_5448"),
        _ar("critic", {"verdict": "approve", "confidence": 0.8, "rationale": "clear CTA"}, cust="cust_5448"),
        _ar("researcher", {"cited": 4}, cust="cust_96e2"),
        _ar("draft", {"hook": "z"}, cust="cust_96e2"),
        _ar("critic", {"verdict": "revise", "confidence": 0.4, "rationale": "weak hook"}, cust="cust_96e2"),
        _ar("jury", {"aggregate": 1.0, "decision": "review"}),
    ]
    return actions, agent_runs


# --------------------------------------------------------------------------- #
# Draft #1 = the REAL first lead (matches the frontend order).
# --------------------------------------------------------------------------- #

def test_draft_one_is_first_lead_by_creation_order():
    actions, agent_runs = _healthy_runs_and_agents()
    state = build_campaign_state(run_id=RUN, action_rows=actions, agent_runs=agent_runs)
    assert state["draft_1"] is not None
    assert state["draft_1"]["index"] == 1
    assert state["draft_1"]["lead_name"] == "Sarah Kim"  # NOT a fabricated other lead
    # The whole ordered list matches the review-queue order.
    assert [d["lead_name"] for d in state["drafts"]] == ["Sarah Kim", "Priya Anand", "Dana Ruiz"]


def test_counts_come_from_real_rows_not_a_guess():
    actions, agent_runs = _healthy_runs_and_agents()
    actions[2]["status"] = "approved"
    state = build_campaign_state(run_id=RUN, action_rows=actions, agent_runs=agent_runs)
    assert state["counts"]["drafts"] == 3          # not "2 when 3 exist"
    assert state["counts"]["pending"] == 2
    assert state["counts"]["approved"] == 1


def test_per_draft_critic_and_research_join_by_customer():
    actions, agent_runs = _healthy_runs_and_agents()
    state = build_campaign_state(run_id=RUN, action_rows=actions, agent_runs=agent_runs)
    d1 = state["draft_1"]
    assert d1["critic_result"]["verdict"] == "approve"
    assert d1["research_used"]["cited"] == 3
    assert d1["skill_used"] == "objection-recovery"
    assert d1["strategy_used"] == "warm win-back with a real offer"
    assert d1["review_status"] == "pending"


def test_describe_draft_names_the_correct_lead_and_why():
    actions, agent_runs = _healthy_runs_and_agents()
    state = build_campaign_state(run_id=RUN, action_rows=actions, agent_runs=agent_runs)
    said = describe_draft(state, 1)
    assert "Sarah Kim" in said
    assert "objection-recovery" in said or "warm win-back" in said


def test_describe_draft_out_of_range_is_honest_not_fabricated():
    actions, agent_runs = _healthy_runs_and_agents()
    state = build_campaign_state(run_id=RUN, action_rows=actions, agent_runs=agent_runs)
    said = describe_draft(state, 9)
    assert "9" in said
    assert "3" in said  # only 3 drafts exist — honest, no invented draft
    assert "Sarah Kim" not in said


# --------------------------------------------------------------------------- #
# "Did strategist / critic run" reflects the REAL agent_runs incl fail-closed failed.
# --------------------------------------------------------------------------- #

def test_did_strategist_run_reflects_failed_status():
    # The reproduced signature: strategist errored (credit-out), every critic errored.
    actions = [_action(1, "cust_fd63", "Sarah Kim")]
    agent_runs = [
        _ar("planner", {"ok": True}),
        _ar("strategist", {"status": "failed", "error": "ModelHTTPError: 402 credit"}),
        _ar("draft", {"hook": ""}, cust="cust_fd63"),
        _ar("critic", {"verdict": "error", "rationale": "critic cell failed"}, cust="cust_fd63"),
        _ar("jury", {"aggregate": 0.0, "decision": "blocked", "status": "failed"}),
    ]
    state = build_campaign_state(run_id=RUN, action_rows=actions, agent_runs=agent_runs)
    # Honest per-agent status: strategist RAN but FAILED (never a fake "done").
    assert state["agents"]["strategist"] == "failed"
    assert state["agents"]["critic"] == "failed"
    # The fail-closed terminal status is surfaced.
    assert state["status"] == "failed"
    assert state["failure_summary"]
    # Spoken summary tells the truth about the strategist.
    said = describe_state(state).lower()
    assert "strategist" in said and "fail" in said


def test_healthy_run_reports_agents_done_and_completed():
    actions, agent_runs = _healthy_runs_and_agents()
    state = build_campaign_state(run_id=RUN, action_rows=actions, agent_runs=agent_runs)
    assert state["agents"]["strategist"] == "done"
    assert state["agents"]["critic"] == "done"
    assert state["status"] == "completed"
    assert state["failure_summary"] == []


def test_empty_run_is_honest_not_fabricated():
    state = build_campaign_state(run_id=RUN, action_rows=[], agent_runs=[])
    assert state["draft_1"] is None
    assert state["counts"]["drafts"] == 0
    said = describe_state(state)
    assert said  # a real "no drafts yet" sentence, not a crash or an invented count


# --------------------------------------------------------------------------- #
# Real-Postgres proof: the same query over seeded DB rows (credit-independent).
# --------------------------------------------------------------------------- #

@pytest.mark.integration
def test_campaign_state_reads_seeded_postgres_rows():
    import os
    import uuid

    import psycopg

    from actions.store import ensure_schema, record_pending_action
    from studio.campaign_state import campaign_state
    from team.store import TeamStore

    dsn = os.environ.get("ENGINE_DATABASE_URL") or "postgresql://scalers:scalers@localhost:5432/scalers"
    run_id = f"team-camp_cstest{uuid.uuid4().hex[:6]}-{uuid.uuid4().hex[:8]}"
    tenant = "ladies8391"
    ensure_schema(dsn)
    ts = TeamStore(dsn)
    ts.setup()

    leads = [("cust_a1", "Sarah Kim"), ("cust_b2", "Priya Anand"), ("cust_c3", "Dana Ruiz")]
    try:
        ts.record_agent_run(id=f"ar_{uuid.uuid4().hex[:12]}", campaign_id="camp_x", run_id=run_id,
                            role="strategist", model="m",
                            input={}, output={"target_angle": "warm win-back"})
        for cust, name in leads:
            record_pending_action(
                tenant_id=tenant, decision_id=None, type="outreach", channel="gmail",
                worker="studio_provided_leads", target=name, draft=f"Hi {name}",
                subject="We miss you", context='{"skill_used": "re-engagement"}',
                conf=None, threshold=None, esc_kind="approval_required", esc_label="x",
                idempotency_key=f"{run_id}:{cust}", run_id=run_id, dsn=dsn,
            )
            ts.record_agent_run(id=f"ar_{uuid.uuid4().hex[:12]}", campaign_id="camp_x", run_id=run_id,
                                role="critic", model="m", input={"customer_id": cust},
                                output={"verdict": "approve", "confidence": 0.9})

        state = campaign_state(run_id, dsn=dsn)
        assert state["counts"]["drafts"] == 3
        assert state["draft_1"]["lead_name"] == "Sarah Kim"  # first by created_at
        assert state["agents"]["strategist"] == "done"
    finally:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            conn.execute("DELETE FROM actions WHERE run_id=%s", (run_id,))
            conn.execute("DELETE FROM agent_runs WHERE run_id=%s", (run_id,))
            conn.commit()
