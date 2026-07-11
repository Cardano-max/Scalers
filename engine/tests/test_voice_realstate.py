"""Voice supervisor is GROUNDED in real run state — CustomerAcq-6bv.

The voice supervisor must answer campaign-state questions from the real DB state, not
guess. These pin: (1) the instructions forbid guessing and define draft #1 as the first
lead; (2) ``voice_instructions_with_state`` / ``voice_state_briefing`` inject the REAL
state (draft #1 = the real first lead, honest agent status) with NO model / realtime key.
"""

from __future__ import annotations

import pytest

import studio.campaign_state as cs
import studio.documents as docs_mod
import studio.voice as voice
from studio.campaign_state import build_campaign_state


def _state_with(lead: str, *, strategist: str) -> dict:
    """A built state whose draft #1 is ``lead`` and whose strategist ran or failed."""
    actions = [{
        "id": "act_1", "target": lead, "subject": f"Hi {lead}", "draft": "body",
        "channel": "gmail", "status": "pending",
        "idempotency_key": "run_x:cust_1",
        "context": '{"skill_used": "objection-recovery"}', "created_at": "2026-07-01T00:00:00+00:00",
    }]
    strat_out = {"status": "failed", "error": "402"} if strategist == "failed" else {"target_angle": "warm win-back"}
    agent_runs = [
        {"role": "strategist", "model": "m", "input": {}, "output": strat_out},
        {"role": "critic", "model": "m", "input": {"customer_id": "cust_1"},
         "output": {"verdict": "error"} if strategist == "failed" else {"verdict": "approve"}},
        {"role": "jury", "model": "m", "input": {},
         "output": {"decision": "blocked" if strategist == "failed" else "review",
                    "aggregate": 0.0 if strategist == "failed" else 1.0}},
    ]
    return build_campaign_state(run_id="run_x", action_rows=actions, agent_runs=agent_runs)


def test_voice_instructions_forbid_guessing_and_define_draft_one():
    t = voice.VOICE_INSTRUCTIONS.lower()
    assert "never invent" in t or "never guess" in t
    assert "draft #1" in t
    # And it must not have gained a SEND-capable tool: the surface is exactly
    # update_plan (edit) + get_run_status / list_conversation_leads (READ-ONLY
    # truth for narration) + request_orchestration (gated launch request) — no
    # publish/send anywhere.
    assert voice.VOICE_TOOL_NAMES == (
        "update_plan", "get_run_status", "list_conversation_leads",
        "request_orchestration",
    )


def test_voice_instructions_with_state_injects_the_real_first_lead(monkeypatch):
    monkeypatch.setattr(docs_mod, "active_docs_index", lambda tenant, dsn=None: [])
    monkeypatch.setattr(cs, "campaign_state", lambda run_id, dsn=None, run_status=None: _state_with("Sarah Kim", strategist="done"))

    out = voice.voice_instructions_with_state("ladies8391", "run_x", dsn=None)
    assert "STATE" in out
    assert "Sarah Kim" in out          # the REAL first lead is in the supervisor's context
    assert "1 draft" in out


def test_state_briefing_tells_the_truth_about_a_failed_strategist(monkeypatch):
    monkeypatch.setattr(cs, "campaign_state", lambda run_id, dsn=None, run_status=None: _state_with("Sarah Kim", strategist="failed"))
    said = voice.voice_state_briefing("run_x", dsn=None).lower()
    assert "strategist" in said and "fail" in said
    assert "sarah kim" in said


def test_voice_instructions_with_no_run_degrades_to_docs_base(monkeypatch):
    monkeypatch.setattr(docs_mod, "active_docs_index", lambda tenant, dsn=None: [])
    out = voice.voice_instructions_with_state("ladies8391", None, dsn=None)
    assert "STATE — the REAL" not in out  # nothing fabricated when there is no run


@pytest.mark.integration
def test_voice_state_briefing_reads_seeded_postgres():
    import os
    import uuid

    import psycopg

    from actions.store import ensure_schema, record_pending_action
    from team.store import TeamStore

    dsn = os.environ.get("ENGINE_DATABASE_URL") or "postgresql://scalers:scalers@localhost:5432/scalers"
    run_id = f"team-camp_vbtest{uuid.uuid4().hex[:6]}-{uuid.uuid4().hex[:8]}"
    ensure_schema(dsn)
    ts = TeamStore(dsn)
    ts.setup()
    try:
        ts.record_agent_run(id=f"ar_{uuid.uuid4().hex[:12]}", campaign_id="camp_x", run_id=run_id,
                            role="strategist", model="m", input={},
                            output={"status": "failed", "error": "ModelHTTPError: 402"})
        record_pending_action(
            tenant_id="ladies8391", decision_id=None, type="outreach", channel="gmail",
            worker="studio_provided_leads", target="Sarah Kim", draft="Hi Sarah",
            subject="We miss you", context='{"skill_used": "re-engagement"}',
            conf=None, threshold=None, esc_kind="approval_required", esc_label="x",
            idempotency_key=f"{run_id}:cust_1", run_id=run_id, dsn=dsn,
        )
        said = voice.voice_state_briefing(run_id, dsn=dsn)
        assert "Sarah Kim" in said
        assert "strategist" in said.lower() and "fail" in said.lower()
    finally:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            conn.execute("DELETE FROM actions WHERE run_id=%s", (run_id,))
            conn.execute("DELETE FROM agent_runs WHERE run_id=%s", (run_id,))
            conn.commit()
