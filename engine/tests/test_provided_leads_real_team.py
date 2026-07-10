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

    monkeypatch.setattr(cr, "_research_enabled", lambda *a, **k: False)
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
            # Honest first-contact copy: these leads carry name-only (no history), so the
            # staged draft must not imply a prior relationship or the anti-fabrication
            # gate (CustomerAcq-wwy.7) correctly refuses it. This fixture tests the
            # strategist/critic RECORDING mechanics, not copy content.
            "channel": "gmail", "target": f"{facts['customer_id']}@lead.example",
            "subject": "Hello from the studio", "draft": "Wanted to reach out and say hello.",
            "grounding": ["name=" + facts["name"], "copy=copywriter_email_cell"],
            "customer_id": facts["customer_id"],
            # The REAL model the cell wrote with — the run must record THIS verbatim,
            # never a hardcoded literal. Deliberately Opus here to catch a stale
            # 'sonnet' literal (model-TRUTH regression).
            "copy_model": "anthropic:claude-opus-4-8",
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


def test_huge_csv_is_capped_never_fans_out(monkeypatch):
    # BLOCKER 2: a 5000-row uploaded CSV must NOT fan out 5000×(analyst+draft+critic).
    # nmh.11 decoupled the provided-leads bound from the compose spine's cap: the
    # executor now caps at ENGINE_COHORT_HARD_CAP (default 1000). Pin it small here
    # so the test stays fast while the intent (bounded fan-out, never 5000x) stays
    # exactly verified.
    _OUTPUT_HARD_CAP = 12
    monkeypatch.setenv("ENGINE_COHORT_HARD_CAP", str(_OUTPUT_HARD_CAP))

    _wire(monkeypatch)
    plan = CampaignPlan(
        lead_source="provided", goal="win back lapsed clients", channels=["gmail"],
        customers={"customer_ids": [f"c{i}" for i in range(5000)], "rows": 5000},
    )
    summary = _execute_provided_leads_sync(plan, "sess1", "ladies8391", None, None)
    roles = _roles(summary)
    assert roles.count("draft") <= _OUTPUT_HARD_CAP
    assert roles.count("analyst") <= _OUTPUT_HARD_CAP
    assert summary["n_pending"] <= _OUTPUT_HARD_CAP
    # The plan step is still the FIRST recorded agent_run.
    assert roles[0] == "planner"


def test_planner_is_recorded_first_with_the_full_blueprint(monkeypatch):
    # HIGH 7: the planner lane — a role='planner' agent_run BEFORE any other role, whose
    # output carries the full executable blueprint (the durable source of truth).
    _wire(monkeypatch)
    summary = _execute_provided_leads_sync(_plan(), "sess1", "ladies8391", None, None)
    ars = summary["agent_runs"]
    assert ars[0]["role"] == "planner"
    assert ars[0]["output"]["blueprint"]["goal"]
    # No analyst/draft/critic precedes the planner.
    first_non_planner = next(i for i, ar in enumerate(ars) if ar["role"] != "planner")
    assert all(ars[i]["role"] == "planner" for i in range(first_non_planner))


def _fake_profile(objection: str):
    from types import SimpleNamespace as NS

    def field(v, s):
        return NS(value=v, signal=s, evidence="the customer said so", evidence_source="conversation")

    return NS(
        primary_objection=field(objection, "stated"),
        umbrella_category=field("past-customer-reactivation", "inferred"),
        readiness_stage=field("preference", "inferred"),
        source="deterministic", had_conversation=True,
        where_customer_sits="considering", best_reengagement_angle="warm win-back",
        grounded_fields=3, insufficient_fields=1,
    )


def test_measured_contradiction_records_a_deterministic_replan_and_flips_the_assumption(monkeypatch):
    # B: the reactivation cohort ASSUMES 'price', but every analyst measures 'trust' ->
    # maybe_replan fires ONCE, recorded as a planner replan agent_run with a DETERMINISTIC
    # id, and the in-memory blueprint's assumption flips. No decorative note.
    import studio.psych_profile as psych

    _wire(monkeypatch)
    monkeypatch.setattr(psych, "analyze_customer", lambda facts, thread=None, **k: _fake_profile("trust"))
    plan = CampaignPlan(
        lead_source="provided", goal="win back lapsed clients", channels=["gmail"],
        target_category="past-customer-reactivation",
        # >= MIN_SAMPLE (3) leads so the measured 'trust' clears the replan guard.
        customers={"customer_ids": ["c1", "c2", "c3"], "rows": 3},
    )
    summary = _execute_provided_leads_sync(plan, "sess1", "ladies8391", None, None)

    replans = [
        ar for ar in summary["agent_runs"]
        if ar["role"] == "planner" and isinstance(ar["output"], dict) and ar["output"].get("replan")
    ]
    assert len(replans) == 1
    rp = replans[0]["output"]["replan"]
    assert rp["from_objection"] == "price" and rp["to_objection"] == "trust"
    # The plan the summary returns has the flipped assumption + the board shows it.
    assert summary["blueprint"]["assumed_dominant_objection"] == "trust"
    assert summary["board"]["contradictions"]  # non-empty (the measured contradiction)


def test_draft_agent_run_records_the_cells_real_model_not_a_literal(monkeypatch):
    # model-TRUTH: the draft agent_run.model must be the model the cell actually wrote
    # with (build_outreach_draft returns copy_model), never a hardcoded literal. The fake
    # cell writes with Opus; a stale 'sonnet' literal would fail this.
    _wire(monkeypatch)
    summary = _execute_provided_leads_sync(_plan(), "sess1", "ladies8391", None, None)
    drafts = [ar for ar in summary["agent_runs"] if ar["role"] == "draft"]
    assert drafts, "no draft recorded"
    for d in drafts:
        assert d["model"] == "anthropic:claude-opus-4-8", d["model"]


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


def _capture_conf(monkeypatch) -> list:
    """Re-point ``record_pending_action`` (after ``_wire``) to capture the ``conf`` each
    staged draft is recorded with — the value the operator sees in the Review Queue."""
    captured: list = []
    monkeypatch.setattr(
        store_mod, "record_pending_action",
        lambda **kw: (captured.append(kw.get("conf")) or f"act_{kw['idempotency_key']}"),
    )
    return captured


def test_critic_confidence_lands_on_staged_draft_conf(monkeypatch):
    """The conf=None complaint: the per-draft critic's verdict + confidence must LAND on
    the staged action's conf. With the critic returning approve@0.9 every staged draft
    carries the mapped ship-quality score (not None)."""
    from studio.agui import _draft_quality_conf

    _wire(monkeypatch)
    captured = _capture_conf(monkeypatch)
    _execute_provided_leads_sync(_plan(), "sess1", "ladies8391", None, None)

    expected = _draft_quality_conf("approve", 0.9)
    assert expected is not None
    assert captured and len(captured) == 2  # one per staged draft
    assert all(c is not None for c in captured)
    assert all(c == expected for c in captured)


def test_failed_critic_leaves_conf_honest_none(monkeypatch):
    """A critic that could not judge -> the staged draft's conf stays honest-unknown
    (None), never a fabricated score."""
    _wire(monkeypatch, crit_exc=RuntimeError("critic 500"))
    captured = _capture_conf(monkeypatch)
    _execute_provided_leads_sync(_plan(), "sess1", "ladies8391", None, None)

    assert captured and len(captured) == 2
    assert all(c is None for c in captured)


def test_deep_research_runs_public_enrichment_per_lead(monkeypatch):
    """research_depth='deep' must ALSO run the cited public-web enrichment for
    EACH lead (the operator's 'research them on socials/LinkedIn' ask) and land
    the honest counts + source urls on that lead's researcher step."""
    import studio.lead_enrichment as le

    _wire(monkeypatch)
    monkeypatch.setattr(cr, "_research_enabled", lambda *a, **k: True)
    calls: list[str] = []

    def _fake_enrich(tenant, cid, *, dsn=None):
        calls.append(cid)
        return {
            "found": [{"text": "runs a bakery in Henderson", "url": "https://yelp.example/x"}],
            "suppressed": 1, "misses": [], "memory_id": "mem_x",
        }

    monkeypatch.setattr(le, "enrich_lead", _fake_enrich)
    summary = _execute_provided_leads_sync(_plan(), "sess", "t_test", None)
    assert calls == ["c1", "c2"]
    researchers = [ar for ar in summary["agent_runs"] if ar["role"] == "researcher"]
    assert len(researchers) == 2
    for ar in researchers:
        pe = ar["output"]["public_enrichment"]
        assert pe["found"] == 1 and pe["suppressed"] == 1 and pe["memory_id"] == "mem_x"
        assert pe["urls"] == ["https://yelp.example/x"]


def test_no_deep_research_means_no_public_enrichment(monkeypatch):
    """With research off, no external lookup fires (operator opt-in stays real)
    and the researcher step says so honestly (public_enrichment None)."""
    import studio.lead_enrichment as le

    _wire(monkeypatch)  # _research_enabled -> False

    def _must_not_run(*a, **k):
        raise AssertionError("enrich_lead must not be called without deep research")

    monkeypatch.setattr(le, "enrich_lead", _must_not_run)
    summary = _execute_provided_leads_sync(_plan(), "sess", "t_test", None)
    researchers = [ar for ar in summary["agent_runs"] if ar["role"] == "researcher"]
    assert researchers and all(
        ar["output"].get("public_enrichment") is None for ar in researchers
    )


# --------------------------------------------------------------------------- #
# Model-failure circuit breaker (operator defect: a 3-draft ask ran 81 leads
# while EVERY model call failed 400, staging junk template drafts).
# --------------------------------------------------------------------------- #

def _model_http_error():
    from pydantic_ai.exceptions import ModelHTTPError

    return ModelHTTPError(
        status_code=400,
        model_name="anthropic:claude-opus-4-8",
        body={"error": {"type": "invalid_request_error", "message": "bad request"}},
    )


def _big_plan(n: int) -> CampaignPlan:
    return CampaignPlan(
        lead_source="provided", goal="win back lapsed clients", channels=["gmail"],
        customers={"customer_ids": [f"c{i}" for i in range(n)], "rows": n},
    )


def test_model_failure_circuit_breaker_stops_the_loop_at_five(monkeypatch):
    # Strategist failed AND every per-lead critic hits a real ModelHTTPError ->
    # the loop STOPS after 5 consecutive model-error leads (never all 12), records
    # ONE honest supervisor step, keeps the already-staged drafts, and the run is
    # FAILED with the breaker reason.
    err = _model_http_error()
    _wire(monkeypatch, strat_exc=err, crit_exc=err)
    summary = _execute_provided_leads_sync(_big_plan(12), "sess1", "ladies8391", None, None)
    roles = _roles(summary)

    assert roles.count("draft") == 5
    assert roles.count("critic") == 5
    assert summary["n_pending"] == 5  # drafts already staged are kept

    sups = [
        ar for ar in summary["agent_runs"]
        if ar["role"] == "supervisor" and (ar["output"] or {}).get("stopped")
    ]
    assert len(sups) == 1
    note = sups[0]["output"]["finding"]
    assert "stopped after 5 lead(s)" in note
    assert "model calls are failing consistently" in note
    assert "ModelHTTPError" in note
    assert "key/credits" in note
    assert "drafts already staged are kept" in note

    # The run is failed WITH the breaker reason surfaced in the failure summary.
    assert summary["run_status"] == "failed"
    assert any(
        f.get("step_id") == "model_failure_circuit_breaker"
        for f in summary["failure_summary"]
    )
    # The ledger still reconciles: 5 drafted + a counted not-attempted remainder.
    ol = summary["output_ledger"]
    assert ol["expected"] == 12 and ol["drafted"] == 5
    assert ol["reconciled"] is True


def test_breaker_needs_the_strategist_to_have_failed_too(monkeypatch):
    # Only the critic failing (strategist fine) is the existing per-draft isolation
    # case: every lead still drafts; the breaker does NOT fire.
    _wire(monkeypatch, crit_exc=_model_http_error())
    summary = _execute_provided_leads_sync(_big_plan(8), "sess1", "ladies8391", None, None)
    roles = _roles(summary)
    assert roles.count("draft") == 8 and roles.count("critic") == 8
    assert not [
        ar for ar in summary["agent_runs"]
        if ar["role"] == "supervisor" and (ar["output"] or {}).get("stopped")
    ]


def test_missing_key_fallback_never_trips_the_breaker(monkeypatch):
    # No key AT ALL: cells fail with a config-style error (no model call was ever
    # attempted) -> deterministic-fallback drafts. The breaker must NOT fire; every
    # lead is still covered (per-draft isolation preserved).
    err = RuntimeError("ANTHROPIC_API_KEY not set; cell never attempted a model call")
    _wire(monkeypatch, strat_exc=err, crit_exc=err)
    summary = _execute_provided_leads_sync(_big_plan(8), "sess1", "ladies8391", None, None)
    roles = _roles(summary)
    assert roles.count("draft") == 8 and roles.count("critic") == 8
    assert summary["n_pending"] == 8
    assert not [
        ar for ar in summary["agent_runs"]
        if ar["role"] == "supervisor" and (ar["output"] or {}).get("stopped")
    ]
    assert all(
        f.get("step_id") != "model_failure_circuit_breaker"
        for f in summary["failure_summary"]
    )
