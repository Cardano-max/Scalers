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
