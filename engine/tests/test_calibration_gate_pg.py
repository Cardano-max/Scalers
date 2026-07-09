"""Integration: REAL calibration gate against REAL Postgres (rvy.8 / D2-as-amended).

Seeds gold examples on CALIBRATION + HOLDOUT for ENGAGEMENT/triage via KbStore,
runs :func:`evals.calibration.run_calibration_gate` end-to-end with a
deterministic confidence_fn, and asserts the verdict + the ``eval_metric`` rows
on the real KB. Marked `integration` + skipif(ENGINE_DATABASE_URL) (dhv.5
convention, mirroring tests/test_eval_gates_pg.py).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest

from evals.calibration import run_calibration_gate
from kb import Direction, Engine, KbStore, RunKind, Split

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

DSN = os.environ.get("ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers")
_SCHEMA = Path(__file__).resolve().parents[2] / "infra" / "initdb" / "03-eval-kb.sql"


@pytest.fixture(scope="module", autouse=True)
def _schema():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(_SCHEMA.read_text(encoding="utf-8"))


@pytest.fixture
def store():
    return KbStore(DSN)


def _tenant() -> str:
    return f"rvy8cal-{uuid.uuid4().hex[:10]}"


def _predictor(example):
    """Deterministic cell-under-test: reads the intended prediction from input
    (input-only — never expected)."""
    return {"triage_class": example.input["pred"]}


def _confidence_fn(example, payload):
    """Deterministic recorded-confidence source: replays the (p_est, routed)
    stored on the example input — the injectable seam the real 4jx.3 pipeline
    plugs into."""
    return (example.input["p_est"], example.input["routed"])


def _seed(store, tenant: str, split: Split, n: int, frac_correct: float,
          p_est: float, routed: float) -> None:
    n_correct = round(n * frac_correct)
    for i in range(n):
        correct = i < n_correct
        store.upsert_gold_example(
            tenant_id=tenant, engine=Engine.ENGAGEMENT, cell="triage",
            input={
                "text": f"{split.value}-{p_est}-{i}", "channel": "comment",
                "pred": "spam", "p_est": p_est, "routed": routed,
            },
            expected={"triage_class": "spam" if correct else "ham"},
            rubric_dimensions=["triage_class"], split=split, label_version=1,
            created_by="rvy8-cal-pg-test",
        )


def test_well_calibrated_pass_records_metrics_on_real_pg(store):
    tenant = _tenant()
    # CALIBRATION (fit) + HOLDOUT (measure), same well-calibrated distribution.
    for split in (Split.CALIBRATION, Split.HOLDOUT):
        _seed(store, tenant, split, 20, 0.90, 0.92, 0.92)
        _seed(store, tenant, split, 20, 0.30, 0.32, 0.32)

    result = run_calibration_gate(
        store, tenant_id=tenant, engine=Engine.ENGAGEMENT, cell="triage",
        dimension="triage_class", predictor=_predictor, confidence_fn=_confidence_fn,
        git_sha="cafe1234",
        confidence_provenance="computed_min_cap_v1",  # 4jx.17 AC2
    )
    assert result.verdict == "PASS"
    assert result.n_pairs == 80

    rows = store.get_metrics(tenant_id=tenant, engine=Engine.ENGAGEMENT.value, cell="triage")
    by_metric = {m.metric: m for m in rows}
    assert set(by_metric) == {"calibration_ece_holdout", "routed_lift"}

    ece_row = by_metric["calibration_ece_holdout"]
    assert ece_row.passed is True
    assert ece_row.direction is Direction.LTE and ece_row.threshold == 0.05
    assert ece_row.value <= 0.05
    assert ece_row.run_kind is RunKind.PER_COMMIT
    assert ece_row.git_sha == "cafe1234"
    # 4jx.17 AC2: the confidence producer is recorded and queryable on real PG.
    assert all(m.confidence_provenance == "computed_min_cap_v1" for m in rows)

    lift_row = by_metric["routed_lift"]
    assert lift_row.passed is True
    assert lift_row.direction is Direction.GTE
    assert lift_row.threshold == pytest.approx(0.80)
    assert lift_row.value >= lift_row.threshold


def test_overconfident_holdout_fails_blocking_on_real_pg(store):
    tenant = _tenant()
    # Fit split says the 0.92 bin is 90% correct; the holdout is only 50% -> the
    # holdout ECE misses the bar with REAL recorded pairs -> FAIL (build-red).
    _seed(store, tenant, Split.CALIBRATION, 20, 0.90, 0.92, 0.92)
    _seed(store, tenant, Split.CALIBRATION, 20, 0.30, 0.32, 0.32)
    _seed(store, tenant, Split.HOLDOUT, 20, 0.50, 0.92, 0.92)
    _seed(store, tenant, Split.HOLDOUT, 20, 0.30, 0.32, 0.32)

    result = run_calibration_gate(
        store, tenant_id=tenant, engine=Engine.ENGAGEMENT, cell="triage",
        dimension="triage_class", predictor=_predictor, confidence_fn=_confidence_fn,
    )
    assert result.verdict == "FAIL"
    assert "calibration_ece_holdout" in {o.metric for o in result.failures}

    rows = store.get_metrics(tenant_id=tenant, metric="calibration_ece_holdout")
    assert len(rows) == 1
    assert rows[0].passed is False and rows[0].value > 0.05


def test_no_gold_pairs_skips_and_writes_nothing_on_real_pg(store):
    tenant = _tenant()  # nothing seeded on CALIBRATION/HOLDOUT for this tenant
    result = run_calibration_gate(
        store, tenant_id=tenant, engine=Engine.ENGAGEMENT, cell="triage",
        dimension="triage_class", predictor=_predictor, confidence_fn=_confidence_fn,
    )
    assert result.verdict == "SKIP"
    assert store.get_metrics(tenant_id=tenant) == []  # neutral: no rows written


def test_decisions_lane_two_gates_end_to_end_on_real_pg(store):
    """4jx.16 on real PG: decisions persisted by the REAL producer (components +
    provenance from 4jx.17) -> route-independent labels -> decisions-lane runner
    -> per-channel eval_metric rows (incl. the ECE-on-routed observability row)
    -> lift_preconditions_ab reads them back at the (tenant, engine, channel)
    grain."""
    import asyncio
    import os as _os

    from autonomy.judges import JudgeScore, JudgeSpec
    from autonomy.produce import produce_and_record_decision_real
    from autonomy.store import PostgresDecisionStore
    from evals.calibration import (
        ECE_ROUTED_OBS_METRIC,
        lift_preconditions_ab,
        run_decision_calibration_gate,
    )

    tenant = _tenant()
    dstore = PostgresDecisionStore(_os.environ["ENGINE_DATABASE_URL"])
    dstore.setup()

    async def runner(spec: JudgeSpec, action: str) -> JudgeScore:
        return JudgeScore(voice=0.95, safety=0.95, appr=0.95, on_voice=True)

    async def produce_all():
        recs = []
        for i in range(24):
            recs.append(await produce_and_record_decision_real(
                dstore, decision_id=f"{tenant}-d{i}", run_id=f"{tenant}-r",
                tenant_id=tenant, channel="instagram", action_kind="post",
                action="a", threshold=0.85, judge_runner=runner,
                self_consistency=0.9,
            ))
        return recs

    recs = asyncio.run(produce_all())
    # Route-independent audit labels (protocol §8): sticky random split, rubric
    # ground truth. Here: jury 0.95 + sc 0.9 -> p_est 0.925, routed 0.9 >= thr;
    # label 22/24 correct (~0.917 >= 0.80 directional bound; ECE needs spread ->
    # synthesize a calibrated low group from the same records' shape is not
    # possible on real decisions, so accept a NOT_PROMOTABLE ECE (degenerate
    # spread) and assert the DIRECTIONAL gate + rows + reader explicitly).
    labeled = [
        (rec, i not in (0, 1), Split.CALIBRATION if i % 2 else Split.HOLDOUT)
        for i, rec in enumerate(recs)
    ]
    result = run_decision_calibration_gate(
        labeled, store=store, tenant_id=tenant, engine=Engine.POSTING,
        channel="instagram", thr=0.85, git_sha="4jx16pg",
    )
    assert result.verdict == "NOT_PROMOTABLE"  # ECE spread degenerate; honest
    lift = next(o for o in result.outcomes if o.metric == "routed_lift")
    assert lift.passed is True and lift.n == 12

    rows = store.get_metrics(tenant_id=tenant, engine=Engine.POSTING.value,
                             channel="instagram")
    by_metric = {m.metric: m for m in rows}
    # lift row recorded (reliable) + the observability row; unreliable ECE not.
    assert set(by_metric) == {"routed_lift", ECE_ROUTED_OBS_METRIC}
    assert by_metric["routed_lift"].passed is True
    assert by_metric["routed_lift"].confidence_provenance == "computed_min_cap_v1"
    obs = by_metric[ECE_ROUTED_OBS_METRIC]
    assert obs.passed is None and obs.threshold is None

    # The 4jx.8 surface: blocked, and the reason names the MISSING ECE row —
    # a not-promotable gate can never be laundered into a lift.
    ok, reasons = lift_preconditions_ab(
        store, tenant_id=tenant, engine=Engine.POSTING, channel="instagram")
    assert not ok
    assert any("calibration_ece_holdout" in r for r in reasons)
