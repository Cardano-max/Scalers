"""Integration: calibration/accuracy gates against REAL Postgres (EVAL-03 / rvy.8).

Proves the gate layer composes with the rvy.2 eval KB end to end: gate outcomes
are written to and read back from ``eval_metric`` (the authoritative gating store),
and a gate run over SMOKE gold examples read from the KB produces the right
verdicts. Marked `integration` + skipif(ENGINE_DATABASE_URL) (dhv.5 convention).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest

from evals.gates import GateStatus, accuracy_gates, brand_voice_gates, calibration_gate
from kb import Engine, KbStore, RunKind, Scope, Split

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
    return f"rvy8-{uuid.uuid4().hex[:10]}"


def test_gate_outcome_persists_to_eval_metric_and_reads_back(store):
    tenant = _tenant()
    pairs = [("a", "a")] * 10 + [("b", "b")] * 10  # P=R=1.0 → PASS
    outs = accuracy_gates(
        pairs, cell="triage", store=store, tenant_id=tenant, engine=Engine.ENGAGEMENT.value,
        label_version=1, git_sha="deadbeef",
    )
    assert all(o.status is GateStatus.PASS for o in outs)
    assert all(o.eval_metric_id for o in outs)  # recorded

    rows = store.get_metrics(tenant_id=tenant, engine=Engine.ENGAGEMENT.value, cell="triage")
    by_metric = {m.metric: m for m in rows}
    assert {"precision", "recall"} <= set(by_metric)
    for m in rows:
        assert m.passed is True
        assert m.threshold == 0.95 and m.direction.value == "GTE"
        assert m.run_kind is RunKind.PER_COMMIT
        assert m.value >= 0.95


def test_failing_gate_records_passed_false(store):
    tenant = _tenant()
    pairs = [("a", "a")] * 10 + [("b", "b")] * 7 + [("a", "b")] * 3  # below bar
    outs = accuracy_gates(pairs, cell="triage", store=store, tenant_id=tenant, engine="ENGAGEMENT")
    assert any(o.status is GateStatus.FAIL for o in outs)
    rows = store.get_metrics(tenant_id=tenant, cell="triage")
    assert any(m.passed is False for m in rows)


def test_calibration_gate_records_ece(store):
    tenant = _tenant()
    pairs = [(1.0, True)] * 15 + [(0.0, False)] * 15  # ECE 0 → PASS
    o = calibration_gate(pairs, store=store, tenant_id=tenant, engine="POSTING", cell="brief")
    assert o.status is GateStatus.PASS
    rows = store.get_metrics(tenant_id=tenant, metric="ece")
    assert len(rows) == 1 and rows[0].direction.value == "LTE" and rows[0].passed is True


def test_skipped_gate_writes_nothing(store):
    tenant = _tenant()
    outs = accuracy_gates([], cell="not-built", store=store, tenant_id=tenant, engine="OUTREACH")
    assert all(o.status is GateStatus.SKIPPED for o in outs)
    assert store.get_metrics(tenant_id=tenant) == []  # neutral: no rows written


def test_end_to_end_gate_over_smoke_gold_examples(store):
    """Seed SMOKE classify examples in the KB, read them back (split=SMOKE), run
    the cell via a deterministic predictor, gate the result. This is the shape the
    rvy.10 smoke loader + rvy.7 solver plug into (KB is the compose seam)."""
    tenant = _tenant()
    # Seed: 12 triage examples, expected class in `expected['class']`.
    examples = [{"text": f"msg-{i}", "class": "spam" if i % 3 == 0 else "ham"} for i in range(12)]
    for ex in examples:
        store.upsert_gold_example(
            tenant_id=tenant, engine=Engine.ENGAGEMENT, cell="triage",
            input={"text": ex["text"]}, expected={"class": ex["class"]},
            rubric_dimensions=["triage"], split=Split.SMOKE, label_version=1,
        )

    gold = store.get_gold_set(tenant_id=tenant, engine=Engine.ENGAGEMENT, split=Split.SMOKE)
    assert len(gold) == 12

    # Deterministic "perfect" predictor stands in for the cell-under-test (rvy.7).
    def predict(g):
        return g.expected["class"]

    pairs = [(predict(g), g.expected["class"]) for g in gold]
    outs = accuracy_gates(pairs, cell="triage", store=store, tenant_id=tenant, engine="ENGAGEMENT")
    assert all(o.status is GateStatus.PASS for o in outs)

    # A broken predictor (always 'ham') flips recall on the spam class → FAIL,
    # exactly the red/green lever rvy.9 needs on smoke data.
    bad_pairs = [("ham", g.expected["class"]) for g in gold]
    bad = accuracy_gates(bad_pairs, cell="triage", store=store, tenant_id=tenant, engine="ENGAGEMENT")
    assert any(o.status is GateStatus.FAIL for o in bad)


def test_end_to_end_brand_voice_from_gold_labels(store):
    """Seed posting examples + 2 raters' on_voice labels, compute κ + on-voice%
    from the KB labels, gate them (the per-promotion brand-voice lane)."""
    tenant = _tenant()
    # 10 examples; both raters agree on_voice on 9, agree off on 1 → κ high, rate 90%.
    for i in range(10):
        on_voice = i != 0  # example 0 is off-voice (both raters agree)
        ex_id = store.upsert_gold_example(
            tenant_id=tenant, engine=Engine.POSTING, cell="brand_voice",
            input={"caption": f"cap-{i}"}, expected={"on_voice": on_voice},
            rubric_dimensions=["voice"], split=Split.HOLDOUT, label_version=1,
        )
        for rater in ("r1", "r2"):
            store.add_gold_label(
                example_id=ex_id, tenant_id=tenant, rater_id=rater,
                dimension="on_voice", label={"on_voice": on_voice}, label_version=1,
            )

    gold = store.get_gold_set(tenant_id=tenant, engine=Engine.POSTING, split=Split.HOLDOUT)
    rater_pairs, consensus = [], []
    for g in gold:
        labels = {lbl.rater_id: lbl.label["on_voice"] for lbl in store.get_labels(tenant_id=tenant, example_id=g.id)}
        rater_pairs.append((labels["r1"], labels["r2"]))
        consensus.append(labels["r1"] and labels["r2"])  # both-agree consensus

    outs = brand_voice_gates(rater_pairs, consensus, store=store, tenant_id=tenant,
                             engine="POSTING", cell="brand_voice")
    st = {o.metric: o.status for o in outs}
    assert st["kappa"] is GateStatus.PASS                       # raters agree → κ=1.0
    assert st["brand_voice_on_voice_rate"] is GateStatus.PASS   # 90% ≥ 0.90 inclusive
    # recorded on the per-promotion lane
    rows = store.get_metrics(tenant_id=tenant, metric="brand_voice_on_voice_rate")
    assert rows[0].run_kind is RunKind.PER_PROMOTION and rows[0].scope is Scope.TENANT
