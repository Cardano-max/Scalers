"""rvy.9 — Phase-2 integration proof: the eval gate fires on a seeded regression.

An eval gate never observed failing is indistinguishable from no gate. This proves
the build-fail wiring end-to-end on the SMOKE set: a deliberately-broken cell
(regression) turns the per-commit verdict RED via a metric (not a crash), a clean
oracle cell is GREEN, metrics land in eval_metric queryable per tenant, and the
verdict is deterministic on re-run.

DB-free tests exercise the gate logic over a fake store (so the done-gate runs it
without Postgres); integration tests run the real gate against the KB on real PG.
"""

from __future__ import annotations

import os

import pytest

from evals.demo_cells import oracle_cell, regressed_triage_cell
from evals.gate import GATES, run_eval_gate
from evals.smoke_gold_set import SMOKE_TENANT, iter_smoke_examples
from kb.schema import Engine, GoldExample, RunKind, Split
from kb.store import content_hash


def _gold_from_smoke() -> list[GoldExample]:
    """Convert the in-memory smoke dataset into GoldExample rows (no DB)."""
    return [
        GoldExample(
            id=e.slug, tenant_id=SMOKE_TENANT, engine=e.engine, cell=e.cell,
            input=e.input, expected=e.expected, rubric_dimensions=e.dimensions,
            split=Split.SMOKE, label_version=1, content_hash=content_hash(e.input),
        )
        for e in iter_smoke_examples()
    ]


class _FakeStore:
    """Minimal store for DB-free gate-logic tests (get_gold_set + record_metric)."""

    def __init__(self, rows: list[GoldExample]) -> None:
        self._rows = rows
        self.metrics: list = []

    def get_gold_set(self, *, tenant_id, engine, split=None, label_version=None, cell=None):
        eng = engine if isinstance(engine, Engine) else Engine(engine)
        return [
            r for r in self._rows
            if r.tenant_id == tenant_id and r.engine is eng
            and (split is None or r.split == split or getattr(split, "value", split) == r.split.value)
        ]

    def record_metric(self, metric):
        self.metrics.append(metric)
        return f"m{len(self.metrics)}"


# ── DB-free: the gate logic ───────────────────────────────────────────────────


def test_clean_change_is_green_db_free():
    store = _FakeStore(_gold_from_smoke())
    res = run_eval_gate(store, oracle_cell, tenant_id=SMOKE_TENANT, record=False)
    assert res.verdict == "PASS", res.message()
    assert not res.failures
    # every required per-commit gate scored and passed
    assert any(o.metric == "triage_recall" for o in res.outcomes)


def test_seeded_regression_is_red_db_free():
    store = _FakeStore(_gold_from_smoke())
    res = run_eval_gate(store, regressed_triage_cell, tenant_id=SMOKE_TENANT, record=False)
    assert res.verdict == "FAIL"
    # it failed via a METRIC, not a crash: numeric values below threshold
    failed_metrics = {o.metric for o in res.failures}
    assert "triage_recall" in failed_metrics or "safety_recall_must_escalate" in failed_metrics
    for o in res.failures:
        assert isinstance(o.value, float) and o.value < o.threshold
    # the failure message names the metric + gold-set version
    msg = res.message()
    assert "triage" in msg and "gold label_version=" in msg and "dataset=" in msg


def test_regression_isolated_to_engagement():
    """The broken cell only touches engagement — outreach/posting gates still pass."""
    store = _FakeStore(_gold_from_smoke())
    res = run_eval_gate(store, regressed_triage_cell, tenant_id=SMOKE_TENANT, record=False)
    passed = {o.metric for o in res.outcomes if o.passed}
    assert "extraction_accuracy" in passed  # outreach untouched -> still green


def test_per_commit_verdict_is_deterministic():
    store = _FakeStore(_gold_from_smoke())
    a = run_eval_gate(store, regressed_triage_cell, tenant_id=SMOKE_TENANT, record=False)
    b = run_eval_gate(store, regressed_triage_cell, tenant_id=SMOKE_TENANT, record=False)
    assert a.verdict == b.verdict
    assert [(o.metric, o.value) for o in a.outcomes] == [(o.metric, o.value) for o in b.outcomes]


def test_empty_gold_is_skip_not_pass():
    store = _FakeStore(_gold_from_smoke())
    res = run_eval_gate(store, oracle_cell, tenant_id="tenant-with-no-gold", record=False)
    assert res.verdict == "SKIP"  # neutral, never a silent pass


def test_brand_voice_is_per_promotion_not_per_commit():
    store = _FakeStore(_gold_from_smoke())
    per_commit = run_eval_gate(store, oracle_cell, tenant_id=SMOKE_TENANT, record=False)
    assert all(o.metric != "brand_voice_onvoice" for o in per_commit.outcomes)
    per_promo = run_eval_gate(store, oracle_cell, tenant_id=SMOKE_TENANT,
                              run_kind=RunKind.PER_PROMOTION, record=False)
    assert any(o.metric == "brand_voice_onvoice" for o in per_promo.outcomes)


def test_registry_separates_cadence():
    kinds = {g.run_kind for g in GATES}
    assert RunKind.PER_COMMIT in kinds and RunKind.PER_PROMOTION in kinds


# ── Integration: the real gate against the KB on real Postgres ────────────────

_pg = pytest.mark.skipif(
    not os.getenv("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)


@pytest.fixture
def kb_store(dsn):
    import psycopg
    from pathlib import Path

    from evals.smoke_gold_set import load_smoke_gold_set
    from kb.store import KbStore

    schema = Path(__file__).resolve().parents[2] / "infra" / "initdb" / "03-eval-kb.sql"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(schema.read_text(encoding="utf-8"))
        conn.execute("TRUNCATE gold_example, gold_label, eval_metric")
    store = KbStore(dsn)
    load_smoke_gold_set(store)
    return store


@pytest.mark.integration
@_pg
def test_clean_passes_regression_fails_and_records_metrics(kb_store):
    clean = run_eval_gate(kb_store, oracle_cell, tenant_id=SMOKE_TENANT, git_sha="clean-sha")
    assert clean.verdict == "PASS", clean.message()

    regressed = run_eval_gate(kb_store, regressed_triage_cell, tenant_id=SMOKE_TENANT, git_sha="bad-sha")
    assert regressed.verdict == "FAIL"
    assert regressed.failures and "triage" in regressed.message()

    # (3) metrics from BOTH runs recorded to eval_metric, queryable per tenant
    rows = kb_store.get_metrics(tenant_id=SMOKE_TENANT, metric="triage_recall")
    assert len(rows) == 2  # one per run
    assert {r.git_sha for r in rows} == {"clean-sha", "bad-sha"}
    assert any(r.passed for r in rows) and any(not r.passed for r in rows)
    assert all(r.run_kind is RunKind.PER_COMMIT for r in rows)


@pytest.mark.integration
@_pg
def test_recorded_metrics_are_tenant_scoped(kb_store):
    run_eval_gate(kb_store, oracle_cell, tenant_id=SMOKE_TENANT)
    assert kb_store.get_metrics(tenant_id="some-other-tenant") == []


@pytest.mark.integration
@_pg
def test_per_commit_verdict_deterministic_on_real_pg(kb_store):
    a = run_eval_gate(kb_store, regressed_triage_cell, tenant_id=SMOKE_TENANT, record=False)
    b = run_eval_gate(kb_store, regressed_triage_cell, tenant_id=SMOKE_TENANT, record=False)
    assert a.verdict == b.verdict == "FAIL"
    assert [(o.metric, round(o.value, 6)) for o in a.outcomes] == [(o.metric, round(o.value, 6)) for o in b.outcomes]


@pytest.mark.integration
@_pg
def test_baseline_rebless_via_new_label_version(kb_store):
    """An intended change re-blesses the baseline through a NEW label_version row;
    the old metric stays as history (reviewed path, not a silent bypass)."""
    run_eval_gate(kb_store, oracle_cell, tenant_id=SMOKE_TENANT)  # v1 baseline
    from kb.schema import Direction, EvalMetric

    kb_store.record_metric(EvalMetric(
        metric="triage_recall", value=0.99, tenant_id=SMOKE_TENANT, engine="ENGAGEMENT",
        cell="triage", threshold=0.95, direction=Direction.GTE, run_kind=RunKind.PER_COMMIT,
        label_version=2,
    ))
    v1 = kb_store.get_metrics(tenant_id=SMOKE_TENANT, metric="triage_recall", label_version=1)
    v2 = kb_store.get_metrics(tenant_id=SMOKE_TENANT, metric="triage_recall", label_version=2)
    assert v1 and v2 and v1[0].value != v2[0].value  # old history intact, new baseline added
