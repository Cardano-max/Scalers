"""The provided-leads (CSV) path runs the REAL team — strategist ONCE + critic PER
draft — recorded as real agent_runs (#1/#2).

The P1 bug: a completed provided-leads run recorded only researcher + draft + jury, so
the Strategist and Critic lanes fell back to "skipped". These tests pin that the path
now ACTUALLY RUNS the real strategy + critic cells and records them as real agent_runs,
so the lanes read ``done`` with real lineage. Cells + DB are faked so this runs offline.

Honesty is also pinned: a cell hiccup records an honest ``failed`` agent_run and the
run continues (never a crash, never a fabricated angle or invented critic praise), and
the strategist's real angle is threaded into the per-lead draft goal (load-bearing).
"""

from __future__ import annotations

import pytest

import actions.store as store_mod
import cells.critic as critic_mod
import cells.strategy as strategy_mod
import memory as memory_mod
import studio.agui as agui
import studio.campaign_runner as runner_mod
import studio.customer_research as cr
import team.store as team_store_mod
from studio.agui import CampaignPlan, _execute_provided_leads_sync
from studio.campaign_runner import AGENT_STATUS_DONE, derive_agent_statuses

_ANGLE = "Win back lapsed clients with a warm, personal note"


class _FakeCell:
    def __init__(self, out, *, exc=None):
        self._out, self._exc = out, exc
        self.model = "anthropic:claude-sonnet-4-6"

    def run_sync(self, prompt):
        if self._exc:
            raise self._exc
        return self._out


class _Strategy:
    target_angle = _ANGLE

    def model_dump(self):
        return {
            "target_angle": self.target_angle, "positioning": "the women-led studio",
            "key_messages": ["you're missed"], "channel_rationale": "email is personal",
        }


class _Verdict:
    value = "approve"


class _Critique:
    verdict = _Verdict()
    confidence = 0.9
    rationale = "On-voice, grounded, clear reply-based CTA."


class _FakeTeamStore:
    def __init__(self, *a, **k):
        self.runs: list[dict] = []

    def setup(self):
        pass

    def record_agent_run(self, **kw):
        self.runs.append(kw)


class _FakeMemory:
    def __init__(self, *a, **k):
        pass

    def ensure_schema(self):
        pass

    def write(self, **kw):
        pass


def _wire(monkeypatch, *, strat_exc=None, crit_exc=None):
    """Fake every DB + cell seam under ``_execute_provided_leads_sync`` so it runs
    offline; return the list that captures the goal each draft was built with."""
    monkeypatch.setattr(memory_mod, "MemoryStore", _FakeMemory)
    monkeypatch.setattr(team_store_mod, "TeamStore", _FakeTeamStore)
    monkeypatch.setattr(store_mod, "ensure_schema", lambda dsn=None: None)
    monkeypatch.setattr(store_mod, "record_pending_action", lambda **kw: f"act_{kw['idempotency_key']}")
    monkeypatch.setattr(runner_mod, "_materialize_runs_row", lambda **kw: False)
    monkeypatch.setattr(agui, "_log_turn", lambda *a, **k: None)
    monkeypatch.setattr(agui, "_persist_plan", lambda *a, **k: None)

    monkeypatch.setattr(cr, "_research_enabled", lambda v: False)
    monkeypatch.setattr(cr, "research_studio", lambda facts, *, enabled: [])
    monkeypatch.setattr(
        cr, "lookup_leads",
        lambda tenant, rows, *, dsn=None, memory_store=None: [
            {"customer_id": r["customer_id"], "name": f"Lead {r['customer_id']}",
             "tattoo_history": [], "persona_traits": {}, "interests": [], "memories": []}
            for r in rows
        ],
    )

    seen_goals: list[str] = []

    def _fake_draft(facts, *, goal="", **kw):
        seen_goals.append(goal)
        return {
            "channel": "gmail", "target": f"{facts['customer_id']}@lead.example",
            "subject": "We miss you", "draft": "Come back for a fresh piece.",
            "grounding": ["name=" + facts["name"], "copy=copywriter_email_cell"],
            "customer_id": facts["customer_id"],
        }

    monkeypatch.setattr(cr, "build_outreach_draft", _fake_draft)
    monkeypatch.setattr(strategy_mod, "build_strategy_prompt", lambda *a, **k: "strategy prompt")
    monkeypatch.setattr(strategy_mod, "build_strategy_cell", lambda **k: _FakeCell(_Strategy(), exc=strat_exc))
    monkeypatch.setattr(critic_mod, "build_critic_cell", lambda **k: _FakeCell(_Critique(), exc=crit_exc))
    return seen_goals


def _plan() -> CampaignPlan:
    return CampaignPlan(
        lead_source="provided", goal="win back lapsed clients", channels=["gmail"],
        customers={"customer_ids": ["c1", "c2"], "rows": 2, "columns": ["name", "email"]},
    )


def _roles(summary) -> list[str]:
    return [ar["role"] for ar in summary["agent_runs"]]


def test_provided_leads_records_strategist_once_and_critic_per_draft(monkeypatch):
    _wire(monkeypatch)
    summary = _execute_provided_leads_sync(_plan(), "sess1", "ladies8391", None, None)
    roles = _roles(summary)

    # Strategist ran exactly ONCE; critic ran once PER draft (2 leads -> 2 critics).
    assert roles.count("strategist") == 1
    assert roles.count("draft") == 2
    assert roles.count("critic") == 2
    assert "jury" in roles
    # Strategist sets the angle BEFORE any draft is written.
    assert roles.index("strategist") < roles.index("draft")

    # The critic recorded a REAL verdict over the actual copy.
    crit = next(ar for ar in summary["agent_runs"] if ar["role"] == "critic")
    assert crit["output"]["verdict"] == "approve"

    # Every canonical lane is now DONE with real lineage — none falls back to skipped.
    statuses = derive_agent_statuses("provided_leads", summary["agent_runs"], "completed")
    assert "queued" not in statuses.values()
    for role in ("strategist", "draft", "critic", "jury"):
        assert statuses[role] == AGENT_STATUS_DONE, role


def test_strategist_angle_is_threaded_into_the_draft_goal(monkeypatch):
    seen_goals = _wire(monkeypatch)
    _execute_provided_leads_sync(_plan(), "sess1", "ladies8391", None, None)
    # The strategist is load-bearing: its real angle flows into every draft's goal.
    assert seen_goals, "no draft was built"
    assert all(_ANGLE in g for g in seen_goals)


def test_strategist_cell_hiccup_records_honest_failed_run_and_continues(monkeypatch):
    seen_goals = _wire(monkeypatch, strat_exc=RuntimeError("model timeout"))
    summary = _execute_provided_leads_sync(_plan(), "sess1", "ladies8391", None, None)
    roles = _roles(summary)

    # The strategist run is still recorded (honest failed), and the run continues:
    # drafts + critics still land. The run never crashes out of existence.
    assert roles.count("strategist") == 1
    strat = next(ar for ar in summary["agent_runs"] if ar["role"] == "strategist")
    assert strat["output"]["status"] == "failed"
    assert "model timeout" in strat["output"]["error"]
    assert roles.count("draft") == 2 and roles.count("critic") == 2
    # No fabricated angle: the draft goal falls back to the base goal.
    assert all(_ANGLE not in g for g in seen_goals)


def test_critic_cell_hiccup_records_honest_error_verdict_never_praise(monkeypatch):
    _wire(monkeypatch, crit_exc=RuntimeError("critic 500"))
    summary = _execute_provided_leads_sync(_plan(), "sess1", "ladies8391", None, None)
    crits = [ar for ar in summary["agent_runs"] if ar["role"] == "critic"]
    assert len(crits) == 2
    for c in crits:
        # Honest failure, never an invented "approve" praise.
        assert c["output"]["verdict"] == "error"
        assert "critic 500" in c["output"]["rationale"]
