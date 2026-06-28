"""End-to-end compose of the rvy.8 gates with the rvy.10 SMOKE gold set, on real
Postgres. Skipped until the smoke loader (qa1, PR #42) is present, so this branch
stays independently green; once merged it runs in the pgvector CI job.

Proves: the gates read the real smoke gold set from the KB (via get_smoke_set),
score each cell, and produce the right verdicts — a perfect predictor passes the
accuracy gates, and the metric-flip examples turn a broken predictor RED (the
exact lever rvy.9 needs). SMOKE is wiring-proof only; it does NOT satisfy 439.
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

# Compose seam: skip cleanly until qa1's loader lands (parallel build).
smoke = pytest.importorskip("evals.smoke_gold_set")

from evals.gates import GateStatus, accuracy_gates, calibration_gate  # noqa: E402
from kb import Engine, KbStore, Split  # noqa: E402

DSN = os.environ.get("ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers")
TENANT = smoke.SMOKE_TENANT


@pytest.fixture(scope="module", autouse=True)
def _loaded():
    from pathlib import Path
    import psycopg
    schema = Path(__file__).resolve().parents[2] / "infra" / "initdb" / "03-eval-kb.sql"
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(schema.read_text(encoding="utf-8"))
    store = KbStore(DSN)
    counts = smoke.load_smoke_gold_set(store)
    # idempotent: a second load doesn't change the counts
    assert smoke.load_smoke_gold_set(store) == counts
    return counts


def test_smoke_loads_all_three_engines(_loaded):
    assert set(_loaded) == {"POSTING", "OUTREACH", "ENGAGEMENT"}
    assert all(v > 0 for v in _loaded.values())


def test_engagement_triage_accuracy_passes_with_perfect_predictor():
    store = KbStore(DSN)
    gold = smoke.get_smoke_set(store, Engine.ENGAGEMENT)
    assert gold, "smoke ENGAGEMENT set is non-empty"
    pairs = [(g.expected["triage_class"], g.expected["triage_class"]) for g in gold]  # perfect
    outs = accuracy_gates(pairs, cell="triage", store=store, tenant_id=TENANT, engine="ENGAGEMENT")
    assert all(o.status is GateStatus.PASS for o in outs)


def test_outreach_extraction_accuracy_passes_with_perfect_predictor():
    store = KbStore(DSN)
    gold = smoke.get_smoke_set(store, Engine.OUTREACH)
    pairs = [(g.expected["extraction"], g.expected["extraction"]) for g in gold]  # perfect field match
    outs = accuracy_gates(pairs, cell="prospect_extract", kind="extraction",
                          store=store, tenant_id=TENANT, engine="OUTREACH")
    assert all(o.status is GateStatus.PASS for o in outs)


def test_ece_gate_runs_on_recorded_confidence_without_error():
    store = KbStore(DSN)
    pairs = []
    for eng in (Engine.POSTING, Engine.OUTREACH, Engine.ENGAGEMENT):
        for g in smoke.get_smoke_set(store, eng):
            conf = g.input.get("recorded_confidence")
            if conf is not None:
                pairs.append((float(conf), True))  # perfect predictor => correct
    # The point is the gate PATH runs on recorded confidence; on tiny synthetic
    # samples it is legitimately PASS or NOT_PROMOTABLE — never a crash, never a
    # false build-break. (Real ECE measurement needs AUTON-02 confidence, Phase 5.)
    o = calibration_gate(pairs, store=store, tenant_id=TENANT, engine="POSTING", cell="copywriter")
    assert o.status in {GateStatus.PASS, GateStatus.FAIL, GateStatus.NOT_PROMOTABLE}


def test_broken_cell_turns_gate_red_on_full_smoke_set():
    """rvy.9 lever: a clean cell passes (proven above); a deliberately-broken cell
    must turn the gate RED on the FULL smoke set (n large enough to be reliable —
    the metric-flip examples are the engineered rows that guarantee the move).
    Run the gate over the whole set, not the flip rows alone (which are below
    min_samples and would read NOT_PROMOTABLE, not FAIL)."""
    assert smoke.metric_flip_examples(), "smoke set defines metric-flip examples"
    store = KbStore(DSN)
    gold = smoke.get_smoke_set(store, Engine.ENGAGEMENT)
    classes = sorted({g.expected["triage_class"] for g in gold})
    assert len(classes) > 1, "need >1 triage class for a meaningful flip"
    broken = classes[0]  # broken cell always predicts one fixed class
    pairs = [(broken, g.expected["triage_class"]) for g in gold]
    outs = accuracy_gates(pairs, cell="triage")  # pure verdict (no store write)
    assert any(o.status is GateStatus.FAIL for o in outs), (
        f"broken predictor over {len(gold)} smoke rows must FAIL the accuracy gate"
    )


def test_smoke_isolation_holdout_query_returns_zero_smoke_rows():
    store = KbStore(DSN)
    holdout = store.get_gold_set(tenant_id=TENANT, engine=Engine.ENGAGEMENT, split=Split.HOLDOUT)
    smoke_rows = store.get_gold_set(tenant_id=TENANT, engine=Engine.ENGAGEMENT, split=Split.SMOKE)
    assert smoke_rows and all(g.split is Split.SMOKE for g in smoke_rows)
    assert all(g.split is not Split.SMOKE for g in holdout)  # real-gate query excludes smoke
