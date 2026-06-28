"""SMOKE gold-set tests (rvy.10).

Two layers:
* DB-free unit tests on the dataset shape (counts, hard floor, flip pairs,
  determinism, DM-escalation rule, unique natural keys).
* Integration tests on real Postgres: load into the rvy.2 KB on the TEST tenant,
  prove split/tenant isolation (a real holdout query returns ZERO smoke rows),
  idempotent re-load, and that loaded expected labels round-trip deterministically.
"""

from __future__ import annotations

import os

import pytest

from evals.smoke_gold_set import (
    SMOKE_SPLIT,
    SMOKE_TENANT,
    get_smoke_set,
    iter_smoke_examples,
    load_smoke_gold_set,
    metric_flip_examples,
)
from kb.schema import Engine, Split
from kb.store import content_hash

_ENGINES = (Engine.POSTING, Engine.OUTREACH, Engine.ENGAGEMENT)


# ── DB-free dataset shape ─────────────────────────────────────────────────────


def _by_engine(engine):
    return [e for e in iter_smoke_examples() if e.engine is engine]


@pytest.mark.parametrize("engine", _ENGINES)
def test_each_engine_has_15_to_30_examples(engine):
    assert 15 <= len(_by_engine(engine)) <= 30


@pytest.mark.parametrize("engine", _ENGINES)
def test_hard_case_floor_at_least_30_percent(engine):
    rows = _by_engine(engine)
    hard = [e for e in rows if e.hard]
    # labeling-protocol §3: >=30% hard band (floor 10 is for the REAL sets; SMOKE
    # is tiny by design, so honor the ratio to keep the hard band represented).
    assert len(hard) / len(rows) >= 0.30, f"{engine}: {len(hard)}/{len(rows)} hard"


@pytest.mark.parametrize("engine", _ENGINES)
def test_metric_flip_pair_per_cell(engine):
    """rvy.9 needs a clear positive AND a clear negative flip per cell."""
    flips = [e for e in _by_engine(engine) if e.flip]
    assert len(flips) >= 2, f"{engine}: need >=2 flip rows, got {len(flips)}"
    if engine is Engine.POSTING:
        vals = {e.expected["on_voice"] for e in flips}
        assert vals == {True, False}
    elif engine is Engine.ENGAGEMENT:
        safeties = {e.expected["reply_safety"] for e in flips}
        assert "safe-to-auto" in safeties and "must-escalate" in safeties
    else:  # OUTREACH
        pers = {e.expected["personalization"] for e in flips}
        assert max(pers) >= 2 and min(pers) == 0


def test_all_dms_must_escalate():
    for e in _by_engine(Engine.ENGAGEMENT):
        if e.input.get("channel") == "dm":
            assert e.expected["reply_safety"] == "must-escalate", e.slug


def test_natural_keys_unique():
    # Idempotency relies on a unique content_hash(input) per (engine, cell).
    keys = [(e.engine, e.cell, content_hash(e.input)) for e in iter_smoke_examples()]
    assert len(keys) == len(set(keys))


def test_slugs_unique():
    slugs = [e.slug for e in iter_smoke_examples()]
    assert len(slugs) == len(set(slugs))


def test_metric_flip_examples_helper_nonempty():
    flips = metric_flip_examples()
    assert len(flips) >= 6  # >=2 per engine x 3
    assert {e.engine for e in flips} == set(_ENGINES)


def test_recorded_confidence_present_on_some_rows():
    withconf = [e for e in iter_smoke_examples() if "recorded_confidence" in e.input]
    assert withconf, "rvy.8 ECE gate wants a few recorded_confidence rows"
    assert all(0.0 <= e.input["recorded_confidence"] <= 1.0 for e in withconf)


# ── Integration: real Postgres KB ─────────────────────────────────────────────

_pg = pytest.mark.skipif(
    not os.getenv("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)


@pytest.fixture
def kb_store(dsn):
    import psycopg

    from kb.store import KbStore
    from pathlib import Path

    schema = Path(__file__).resolve().parents[2] / "infra" / "initdb" / "03-eval-kb.sql"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(schema.read_text(encoding="utf-8"))
        conn.execute("TRUNCATE gold_example, gold_label, eval_metric")
    return KbStore(dsn)


@pytest.mark.integration
@_pg
def test_load_counts_per_engine(kb_store):
    counts = load_smoke_gold_set(kb_store)
    for engine in _ENGINES:
        assert 15 <= counts[engine.value] <= 30
        assert len(get_smoke_set(kb_store, engine)) == counts[engine.value]


@pytest.mark.integration
@_pg
def test_reload_is_idempotent(kb_store):
    first = load_smoke_gold_set(kb_store)
    second = load_smoke_gold_set(kb_store)  # same natural keys
    assert first == second
    total = sum(len(get_smoke_set(kb_store, e)) for e in _ENGINES)
    assert total == len(iter_smoke_examples())  # no duplicate rows


@pytest.mark.integration
@_pg
def test_real_holdout_query_returns_zero_smoke_rows(kb_store):
    """ISOLATION: smoke + a real holdout row both present -> a HOLDOUT query for
    the real gate returns zero smoke rows."""
    load_smoke_gold_set(kb_store)
    # seed a non-smoke (holdout) row for the same tenant/engine
    kb_store.upsert_gold_example(
        tenant_id=SMOKE_TENANT, engine=Engine.POSTING, cell="copywriter",
        input={"kind": "caption", "text": "a real holdout caption"},
        expected={"on_voice": True}, split=Split.HOLDOUT,
    )
    holdout = kb_store.get_gold_set(tenant_id=SMOKE_TENANT, engine=Engine.POSTING, split=Split.HOLDOUT)
    assert len(holdout) == 1
    assert all(r.split is Split.HOLDOUT for r in holdout)
    smoke = get_smoke_set(kb_store, Engine.POSTING)
    assert all(r.split is SMOKE_SPLIT for r in smoke)
    # the holdout row is not in the smoke set and vice-versa
    assert {r.id for r in holdout}.isdisjoint({r.id for r in smoke})


@pytest.mark.integration
@_pg
def test_smoke_does_not_leak_into_other_tenant(kb_store):
    load_smoke_gold_set(kb_store)
    for engine in _ENGINES:
        assert get_smoke_set(kb_store, engine, tenant_id="real-client-xyz") == []


@pytest.mark.integration
@_pg
def test_expected_labels_round_trip_deterministically(kb_store):
    load_smoke_gold_set(kb_store)
    posting = {tuple(sorted(e.input.items())): e for e in iter_smoke_examples() if e.engine is Engine.POSTING}
    for row in get_smoke_set(kb_store, Engine.POSTING):
        src = posting[tuple(sorted(row.input.items()))]
        assert row.expected == src.expected  # deterministic, no drift
        labels = kb_store.get_labels(tenant_id=SMOKE_TENANT, example_id=row.id)
        assert any(lbl.rater_id == "smoke-oracle" and lbl.label == src.expected for lbl in labels)
