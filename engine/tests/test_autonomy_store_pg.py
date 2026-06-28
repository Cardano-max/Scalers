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
