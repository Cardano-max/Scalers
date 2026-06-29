"""Real-jury persistence (AUTON-01 / 4jx.2 on the 4jx.10 columns) — real Postgres.

Proves the real per-judge signals land in the new autonomy_jury / autonomy_decisions
columns (reliability_weight, hard_fail, self_consistency) — real rows where the stub
wrote uniform ones.
"""

from __future__ import annotations

import asyncio
import os

import psycopg
import pytest

from autonomy.judges import JudgeScore, JudgeSpec
from autonomy.produce import produce_and_record_decision_real
from autonomy.store import PostgresDecisionStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.getenv("ENGINE_DATABASE_URL"), reason="requires Postgres"),
]


def _runner(score_by_name):
    async def run(spec: JudgeSpec, action: str) -> JudgeScore:
        return score_by_name[spec.name]
    return run


@pytest.fixture
def store(dsn):
    st = PostgresDecisionStore(dsn)
    st.setup()
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE autonomy_jury, autonomy_decisions CASCADE")
    return st


def _clean_scores():
    return {n: JudgeScore(voice=0.95, safety=0.95, appr=0.95, on_voice=True)
            for n in ("opus-strict", "opus-charitable", "ollama-cross")}


def test_real_jury_rows_persist_reliability_weight_and_hard_fail(store, dsn):
    # One judge hard-fails appropriateness; all weights uniform.
    scores = _clean_scores()
    scores["opus-strict"] = JudgeScore(voice=0.95, safety=0.95, appr=0.2, on_voice=True, appr_hard_fail=True)
    rec = asyncio.run(
        produce_and_record_decision_real(
            store, decision_id="d1", run_id="r1", tenant_id="ladies8391",
            channel="instagram", action_kind="post", action="a post",
            threshold=0.85, judge_runner=_runner(scores),
        )
    )
    # The decision honored the floor.
    assert rec.decision.value == "review" and "hard-fail" in rec.esc.label

    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        jury = conn.execute(
            "SELECT judge, reliability_weight, hard_fail FROM autonomy_jury "
            "WHERE decision_id='d1' ORDER BY judge"
        ).fetchall()
        dec = conn.execute(
            "SELECT self_consistency FROM autonomy_decisions WHERE decision_id='d1'"
        ).fetchone()

    assert len(jury) == 3
    by_judge = {r["judge"]: r for r in jury}
    assert by_judge["opus-strict"]["hard_fail"] is True        # the per-judge floor flag
    assert by_judge["opus-charitable"]["hard_fail"] is False
    assert all(r["reliability_weight"] == 1.0 for r in jury)    # real weights, not NULL
    # self_consistency column exists + is writable (None until 4jx.3 computes it).
    assert "self_consistency" in dec


def test_decision_round_trips_with_new_columns(store):
    rec = asyncio.run(
        produce_and_record_decision_real(
            store, decision_id="d2", run_id="r2", tenant_id="t",
            channel="instagram", action_kind="post", action="x",
            threshold=0.85, judge_runner=_runner(_clean_scores()),
        )
    )
    got = store.get_decision("d2")
    assert got is not None
    assert {v.family for v in got.jury} >= {"anthropic", "ollama"}
    assert all(v.reliability_weight == 1.0 for v in got.jury)  # weight restored on read
    assert got.self_consistency == rec.self_consistency  # None round-trips
