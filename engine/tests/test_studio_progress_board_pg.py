"""On-demand progress board + planner-output blueprint — Postgres integration (skips w/o DB).

Proves the derived substrate end-to-end against a REAL cluster WITHOUT any mutable board /
blueprint table: the planner's blueprint is durable AS a ``role='planner'`` agent_run
output, and :func:`compute_progress_board` counts the SAME real rows (agent_runs / actions /
runs) on demand. No LLM — the planner core is deterministic. Mirrors the other ``*_pg``
tests' connect-or-skip guard.
"""

from __future__ import annotations

import os
import uuid

import pytest

_DSN = (
    os.environ.get("ENGINE_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or "postgresql://scalers:scalers@localhost:5432/scalers"
)


def _db_or_skip():
    try:
        import psycopg

        psycopg.connect(_DSN, connect_timeout=3).close()
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"no Postgres for integration test: {exc}")


def test_blueprint_lives_in_planner_run_and_board_computes_on_demand_pg() -> None:
    _db_or_skip()
    from actions.store import ensure_schema, record_pending_action
    from studio.agui import CampaignPlan, _planner_run_output, plan_campaign
    from studio.campaign_blueprint import offer_rule_for
    from studio.campaign_runner import _materialize_runs_row
    from studio.documents import add_document
    from studio.offers import OFFERS_DOC_KIND
    from studio.progress_board import compute_progress_board
    from team.store import TeamStore

    tenant = f"test_{uuid.uuid4().hex[:8]}"
    campaign_id = f"camp_{uuid.uuid4().hex[:8]}"
    run_id = f"team-{campaign_id}-{uuid.uuid4().hex[:8]}"
    # A REAL (operator-provided) offers doc — the seeded MOCK never substantiates a live
    # plan under 65w.14 (is_real_offer_source), so the blueprint's offer_logic must be
    # grounded in a real doc for a price rule to exist at all.
    assert add_document(
        tenant, "Studio Offers (real)",
        "- code: INKED15 | description: 15% off returning-client sessions"
        " | discount: 15% | applies_to: any | kind: discount",
        kind=OFFERS_DOC_KIND, source="operator",
        doc_id=f"doc_offers_{tenant}_real", dsn=_DSN,
    )

    plan = CampaignPlan(
        goal="win back lapsed clients", target_category="past-customer-reactivation",
        scope="whole studio", channels=["sms"], output_count=1,
    )
    blueprint = plan_campaign(plan, tenant, _DSN)  # deterministic (no LLM)
    # plan_campaign wrote the resolved assumption back onto the shared plan.
    assert plan.assumed_objection == "price"
    price = offer_rule_for(blueprint, "price")
    assert price is not None and price.offer_code and price.substantiated

    ts = TeamStore(_DSN)
    ts.setup()
    ensure_schema(_DSN)
    # The blueprint is durable AS the planner agent_run output — no blueprint table.
    ts.record_agent_run(
        id=f"ar_{uuid.uuid4().hex[:16]}", campaign_id=campaign_id, run_id=run_id,
        role="planner", model=blueprint.planner_model,
        input={"goal": blueprint.goal}, output=_planner_run_output(blueprint),
    )
    ts.record_agent_run(
        id=f"ar_{uuid.uuid4().hex[:16]}", campaign_id=campaign_id, run_id=run_id,
        role="analyst", model="grounded_rules", input={"customer_id": "c1"},
        output={"primary_objection": "price", "objection_signal": "stated"},
    )
    ts.record_agent_run(
        id=f"ar_{uuid.uuid4().hex[:16]}", campaign_id=campaign_id, run_id=run_id,
        role="draft", model="grounded_template",
        input={"customer_id": "c1", "channel": "sms"}, output={"hook": "x"},
    )
    record_pending_action(
        tenant_id=tenant, decision_id=None, type="outreach", channel="sms",
        worker="studio_provided_leads", target="c1", draft="hi", subject=None, conf=0.5,
        threshold=None, esc_kind="approval_required", esc_label="review",
        idempotency_key=f"{run_id}:c1", run_id=run_id, dsn=_DSN,
    )
    agent_runs = [
        {"role": "planner", "input": {}, "output": {}},
        {"role": "analyst", "input": {"customer_id": "c1"},
         "output": {"primary_objection": "price", "objection_signal": "stated"}},
        {"role": "draft", "input": {"customer_id": "c1", "channel": "sms"}, "output": {}},
    ]
    _materialize_runs_row(dsn=_DSN, run_id=run_id, tenant_id=tenant, agent_runs=agent_runs)

    # The board is computed ON DEMAND from the real rows (no board table).
    board = compute_progress_board(tenant, plan, _DSN)
    assert board.run_id == run_id
    assert board.leads_done == 1
    # The objection was measured AND its lead produced a staged draft -> addressed.
    assert board.objections_addressed == ["price"]

    # The blueprint is retrievable from the durable planner agent_run output.
    rows = ts.list_agent_runs(run_id)
    planner = next(r for r in rows if r["role"] == "planner")
    assert planner["output"]["blueprint"]["assumed_dominant_objection"] == "price"
    assert planner["output"]["blueprint"]["per_channel_quota"] == {"sms": 1}

    # And it also round-trips through the dedicated blueprint_store row (the authored plan).
    from studio import blueprint_store

    blueprint_store.upsert_blueprint(
        run_id, blueprint.model_dump(), campaign_id=campaign_id, tenant_id=tenant,
        planner_model=blueprint.planner_model, dsn=_DSN,
    )
    stored = blueprint_store.get_blueprint(run_id, dsn=_DSN)
    assert stored is not None
    assert stored["planner_model"] == blueprint.planner_model
    assert stored["state"]["assumed_dominant_objection"] == "price"
    assert stored["state"]["per_channel_quota"] == {"sms": 1}
