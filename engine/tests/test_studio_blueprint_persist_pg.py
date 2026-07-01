"""Blueprint + progress-board persistence — Postgres integration (skips without a DB).

Proves the durable substrate end-to-end against a REAL cluster: the planner's blueprint
(offer_logic grounded in a real seeded offers doc) round-trips through ``blueprint_store``,
and a computed :class:`ProgressBoard` round-trips through ``progress_board_store``. No LLM
— the planner core is deterministic, so this runs in CI without a key. Mirrors the other
``*_pg`` tests' connect-or-skip guard.
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

        conn = psycopg.connect(_DSN, connect_timeout=3)
        conn.close()
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"no Postgres for integration test: {exc}")


def test_blueprint_and_board_roundtrip_pg() -> None:
    _db_or_skip()
    from studio import blueprint_store, progress_board_store
    from studio.agui import CampaignPlan
    from studio.campaign_blueprint import build_blueprint, offer_rule_for
    from studio.offers import seed_offers_doc
    from studio.progress_board import ProgressBoard, compute_board

    tenant = f"test_{uuid.uuid4().hex[:8]}"
    run_id = f"team-camp_{uuid.uuid4().hex[:8]}-{uuid.uuid4().hex[:8]}"

    # Seed the tenant's REAL offers doc so offer_logic grounds on real rows (not a mock).
    assert seed_offers_doc(tenant, dsn=_DSN) is not None

    bp = build_blueprint(
        CampaignPlan(
            goal="win back lapsed clients",
            target_category="past-customer-reactivation",
            scope="whole studio",
            channels=["sms", "email"],
            output_count=4,
        ),
        tenant, _DSN, run_id=run_id, use_llm=False,
    )
    # Grounded in the seeded offers doc: price -> a real discount code.
    price = offer_rule_for(bp, "price")
    assert price is not None and price.offer_code and price.substantiated is True

    # Persist + read back the blueprint.
    blueprint_store.upsert_blueprint(run_id, bp.model_dump(), tenant_id=tenant, dsn=_DSN)
    stored = blueprint_store.get_blueprint(run_id, dsn=_DSN)
    assert stored is not None
    assert stored["state"]["assumed_dominant_objection"] == "price"
    assert stored["state"]["per_channel_quota"] == {"sms": 2, "email": 2}

    # Compute + persist + read back a board.
    agent_runs = [
        {"role": "analyst", "output": {"primary_objection": "price", "objection_signal": "stated"}},
        {"role": "draft", "input": {"channel": "sms"}, "output": {"hook": "x"}},
    ]
    board = compute_board(run_id, None, agent_runs, [], CampaignPlan(output_count=4))
    progress_board_store.upsert_board(run_id, board.model_dump(), tenant_id=tenant, dsn=_DSN)
    back = progress_board_store.get_board(run_id, dsn=_DSN)
    assert back is not None
    assert ProgressBoard(**back["state"]).objections_resolved == ["price"]
