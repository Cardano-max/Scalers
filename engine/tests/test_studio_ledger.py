"""Output-count reconciliation + per-row SKIP LEDGER (P2-D, CustomerAcq-65w.8), and the
dossier+skill wiring on the draft agent_runs (P2-B/-C), exercised end-to-end through the
REAL ``_execute_provided_leads_sync`` with every DB/cell seam faked so it runs offline.

Pins: 10 leads = 10 drafts, or a row-level skip reason for every gap (no silent undercount);
each staged draft carries a linked dossier + the selected marketing skill; drafts stay HELD.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

import actions.store as store_mod
import cells.critic as critic_mod
import cells.strategy as strategy_mod
import memory as memory_mod
import studio.agui as agui
import studio.customer_research as cr
import studio.psych_profile as psych
import team.store as team_store_mod
from studio.agui import CampaignPlan, _execute_provided_leads_sync


class _FakeCell:
    def __init__(self, out):
        self._out = out
        self.model = "anthropic:claude-sonnet-4-6"

    def run_sync(self, prompt):
        return self._out


class _Strategy:
    target_angle = "Win back with a warm, personal note"

    def model_dump(self):
        return {"target_angle": self.target_angle, "positioning": "women-led studio",
                "key_messages": ["you're missed"], "channel_rationale": "email is personal"}


class _Critique:
    verdict = NS(value="approve")
    confidence = 0.9
    rationale = "On-voice, grounded, clear CTA."


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


def _f(v, s, e=""):
    return NS(value=v, signal=s, evidence=e, evidence_source="conversation")


def _profile_for(facts):
    """A deterministic grounded profile keyed off the lead's ``_obj`` marker."""
    if facts.get("_obj"):
        return NS(primary_objection=_f(facts["_obj"], "stated", "it's out of my budget"),
                  umbrella_category=_f("past-customer-reactivation", "inferred"),
                  readiness_stage=_f("preference", "inferred"), had_conversation=True,
                  where_customer_sits="considering", best_reengagement_angle="warm",
                  source="deterministic", grounded_fields=3, insufficient_fields=1)
    return NS(primary_objection=_f("none-found", "insufficient-signal"),
              umbrella_category=_f("recurring-customer", "inferred"),
              readiness_stage=_f("preference", "inferred"), had_conversation=False,
              where_customer_sits="", best_reengagement_angle="", source="deterministic",
              grounded_fields=1, insufficient_fields=2)


def _wire(monkeypatch, leads):
    monkeypatch.setattr(memory_mod, "MemoryStore", _FakeMemory)
    monkeypatch.setattr(team_store_mod, "TeamStore", _FakeTeamStore)
    monkeypatch.setattr(store_mod, "ensure_schema", lambda dsn=None: None)
    captured: dict[str, dict] = {}

    def _rec_action(**kw):
        captured[kw["idempotency_key"]] = kw
        return f"act_{kw['idempotency_key']}"

    monkeypatch.setattr(store_mod, "record_pending_action", _rec_action)
    monkeypatch.setattr(agui, "_log_turn", lambda *a, **k: None)
    monkeypatch.setattr(agui, "_persist_plan", lambda *a, **k: None)
    from studio import campaign_runner as runner_mod
    monkeypatch.setattr(runner_mod, "_materialize_runs_row", lambda **kw: False)
    monkeypatch.setattr(cr, "_research_enabled", lambda v: False)
    monkeypatch.setattr(cr, "research_studio", lambda facts, *, enabled: [])
    by_id = {leadv["customer_id"]: leadv for leadv in leads}
    monkeypatch.setattr(
        cr, "lookup_leads",
        lambda tenant, rows, *, dsn=None, memory_store=None: [
            by_id[r["customer_id"]] for r in rows if r["customer_id"] in by_id
        ],
    )
    monkeypatch.setattr(strategy_mod, "build_strategy_prompt", lambda *a, **k: "p")
    monkeypatch.setattr(strategy_mod, "build_strategy_cell", lambda **k: _FakeCell(_Strategy()))
    monkeypatch.setattr(critic_mod, "build_critic_cell", lambda **k: _FakeCell(_Critique()))
    monkeypatch.setattr(psych, "analyze_customer", lambda facts, thread=None, **k: _profile_for(facts))
    return captured


def _lead(cid, name, *, email=None, phone=None, ig=None, obj=None, segment=None, interest=None):
    return {"customer_id": cid, "name": name, "email": email, "phone": phone,
            "ig_handle": ig, "city": "Austin", "interests": [interest] if interest else [],
            "customer_type": segment, "persona_traits": {}, "tattoo_history": [],
            "memories": [], "_obj": obj}


def _drafts(summary):
    return [ar for ar in summary["agent_runs"] if ar["role"] == "draft"]


def test_ledger_reconciles_with_row_level_skip_reasons(monkeypatch):
    leads = [
        _lead("c1", "Sarah Kim", email="sarah@x.com", obj="price"),
        _lead("c2", "Noor Vance", email="noor@x.com", segment="recurring regular"),
        _lead("c3", None, email=None, phone=None, ig=None),  # empty/unreachable row -> skip
    ]
    _wire(monkeypatch, leads)
    plan = CampaignPlan(
        lead_source="provided", goal="win back lapsed clients", channels=["gmail"],
        output_count=4,
        customers={"customer_ids": ["c1", "c2", "c3", "c404"], "rows": 4},
    )
    summary = _execute_provided_leads_sync(plan, "sess", "ladies8391", None, None)

    ledger = summary["output_ledger"]
    assert ledger["expected"] == 4
    assert ledger["drafted"] == 2                     # c1, c2 drafted
    assert ledger["reconciled"] is True               # 2 drafted + 2 skipped == 4
    reasons = {s["lead"]: s["reason"] for s in ledger["skipped"]}
    assert "no contact method" in reasons["c3"]        # empty/unreachable row
    assert "not found in database" in reasons["c404"]
    # No silent undercount: every expected row is drafted or has a reason.
    assert ledger["drafted"] + len(ledger["skipped"]) == ledger["expected"]
    # Board mirrors the ledger.
    assert summary["board"]["drafted"] == 2 and summary["board"]["expected"] == 4
    assert summary["board"]["skip_ledger"]


def test_each_draft_links_a_dossier_and_the_right_skill(monkeypatch):
    leads = [
        _lead("c1", "Sarah Kim", email="sarah@x.com", obj="price"),
        _lead("c2", "Noor Vance", email="noor@x.com", segment="recurring regular"),
    ]
    captured = _wire(monkeypatch, leads)
    plan = CampaignPlan(
        lead_source="provided", goal="win back", channels=["gmail"], output_count=2,
        customers={"customer_ids": ["c1", "c2"], "rows": 2},
    )
    summary = _execute_provided_leads_sync(plan, "sess", "ladies8391", None, None)

    drafts = _drafts(summary)
    assert len(drafts) == 2
    by_cust = {ar["input"]["customer_id"]: ar["output"] for ar in drafts}
    # Price-objection lead -> objection-recovery; recurring regular -> loyalty-touchup.
    assert by_cust["c1"]["skill_used"] == "objection-recovery"
    assert by_cust["c2"]["skill_used"] == "loyalty-touchup"
    # Each draft carries a linked dossier whose identity traces to real data.
    assert by_cust["c1"]["dossier"]["name"]["value"] == "Sarah Kim"
    assert by_cust["c1"]["dossier"]["likely_objection"]["value"] == "price"
    # The staged Review-Queue row LINKS the dossier via context (durable deep-link).
    # Find the c1 action by its idempotency key suffix.
    c1_key = next(k for k in captured if k.endswith(":c1"))
    import json
    ctx = json.loads(captured[c1_key]["context"])
    assert ctx["skill_used"] == "objection-recovery"
    assert ctx["dossier"]["email"]["value"] == "sarah@x.com"


def test_drafts_stay_held_pending(monkeypatch):
    leads = [_lead("c1", "Sarah", email="s@x.com", obj="price")]
    captured = _wire(monkeypatch, leads)
    plan = CampaignPlan(lead_source="provided", goal="g", channels=["gmail"],
                        output_count=1, customers={"customer_ids": ["c1"], "rows": 1})
    summary = _execute_provided_leads_sync(plan, "sess", "ladies8391", None, None)
    assert summary["n_pending"] == 1
    # The staged action was created via the approve-first path (esc_kind approval_required).
    c1_key = next(k for k in captured if k.endswith(":c1"))
    assert captured[c1_key]["esc_kind"] == "approval_required"
