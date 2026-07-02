"""Postgres integration for the autonomy decision store (OBS-02).

Proves the decision record persists and reads back on a REAL Postgres — the AC's
"queryable on real PG" — including the normalized one-row-per-judge jury table.

Marked ``integration`` (routes to CI's pgvector job, excluded from the DB-free
unit run) AND ``skipif`` no ``ENGINE_DATABASE_URL`` (safe to invoke locally
without a DB) — same convention as test_postgres_integration.py, so it cannot
silently never-run in CI.
"""

from __future__ import annotations

import os
import uuid

import pytest

from autonomy.decision import EscKind, SafetyVerdict
from autonomy.jury import expected_judge_count
from autonomy.produce import produce_and_record_decision
from autonomy.store import PostgresDecisionStore
from harness.state import Gate, RouteDecision

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]


def _store() -> PostgresDecisionStore:
    store = PostgresDecisionStore(os.environ["ENGINE_DATABASE_URL"])
    store.setup()
    return store


def test_decision_round_trips_on_real_postgres():
    store = _store()
    run_id = f"obs-{uuid.uuid4().hex[:12]}"
    decision_id = f"{run_id}-a0"

    produce_and_record_decision(
        store,
        decision_id=decision_id,
        run_id=run_id,
        tenant_id="itest",
        channel="instagram",
        action_kind="post",
        base_confidence=0.9,
        threshold=0.85,
        allow_stub_auto=True,  # 4jx.17: stub auto is demo-flag-only
    )

    # Read back from Postgres and compare the full record.
    got = store.get_decision(decision_id)
    assert got is not None
    assert got.decision is RouteDecision.AUTO
    assert got.esc.kind is EscKind.NONE
    assert got.pooled_confidence == pytest.approx(0.9)
    assert got.threshold == pytest.approx(0.85)
    assert got.agreement == pytest.approx(1.0)
    assert got.safety_verdict is SafetyVerdict.PASS
    # One row per cross-family judge, read back in full.
    assert len(got.jury) == expected_judge_count()
    assert {v.family for v in got.jury} == {"anthropic", "openai", "google", "deepseek"}
    assert all(v.voice == pytest.approx(0.9) for v in got.jury)

    # Queryable by run.
    listed = store.list_decisions(run_id)
    assert [r.decision_id for r in listed] == [decision_id]


def test_escalation_and_gates_persist_on_real_postgres():
    store = _store()
    run_id = f"obs-{uuid.uuid4().hex[:12]}"
    decision_id = f"{run_id}-a0"

    produce_and_record_decision(
        store,
        decision_id=decision_id,
        run_id=run_id,
        tenant_id="itest",
        channel="gmail",
        action_kind="email",
        base_confidence=0.95,
        gates=[Gate(name="suppression", passed=True), Gate(name="length", passed=False)],
    )

    got = store.get_decision(decision_id)
    assert got.decision is RouteDecision.REGENERATE  # failed gate
    assert got.esc.kind is EscKind.GATE
    assert {(g.label, g.ok) for g in got.gates} == {("suppression", True), ("length", False)}


def test_confidence_components_and_provenance_round_trip_on_real_postgres():
    """4jx.17 AC1+AC2 on real PG: the additive migration lands the JSONB
    components + provenance columns; a decision persists both and reads them
    back — the LiftController's per-channel provenance query and the rvy.8
    offline recompute's p_est source are durable."""
    import asyncio

    from autonomy.judges import JudgeScore, JudgeSpec
    from autonomy.produce import PROVENANCE_STUB_JURY, produce_and_record_decision_real

    store = _store()
    run_id = f"obs-{uuid.uuid4().hex[:12]}"
    decision_id = f"{run_id}-a0"

    async def runner(spec: JudgeSpec, action: str) -> JudgeScore:
        return JudgeScore(voice=0.95, safety=0.95, appr=0.95, on_voice=True)

    asyncio.run(produce_and_record_decision_real(
        store,
        decision_id=decision_id, run_id=run_id, tenant_id="itest",
        channel="instagram", action_kind="post", action="a",
        threshold=0.85, judge_runner=runner, self_consistency=0.9,
    ))
    got = store.get_decision(decision_id)
    from autonomy.confidence import PROVENANCE_COMPUTED

    assert got.confidence_provenance == PROVENANCE_COMPUTED
    comps = got.confidence_components
    assert comps is not None
    assert set(comps) == {"raw", "p_est", "jury_quality", "self_consistency", "cap_bind_delta"}
    assert comps["p_est"] == pytest.approx(0.925)          # calibrated pooled estimate
    assert got.pooled_confidence == pytest.approx(0.9)     # capped routed value differs

    # Stub decisions persist their provenance too (and NULL components).
    stub_id = f"{run_id}-a1"
    produce_and_record_decision(
        store, decision_id=stub_id, run_id=run_id, tenant_id="itest",
        channel="instagram", action_kind="post", base_confidence=0.9,
    )
    stub = store.get_decision(stub_id)
    assert stub.confidence_provenance == PROVENANCE_STUB_JURY
    assert stub.confidence_components is None
