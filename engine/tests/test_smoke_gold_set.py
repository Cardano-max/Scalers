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
import uuid

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


def test_load_prunes_to_current_hashes_db_free():
    """CustomerAcq-wwy.5 (DB-free wiring guard, runs in the done-gate): the loader
    is authoritative — it prunes SMOKE rows to EXACTLY the current set's content
    hashes, so a relabel can't leave stale rows behind on a persistent KB."""
    from kb.store import content_hash

    class _RecordingStore:
        """Captures the prune call without a database."""

        def __init__(self) -> None:
            self.pruned: tuple | None = None

        def upsert_gold_example(self, **_kw) -> str:
            return "example-id"

        def add_gold_label(self, **_kw) -> str:
            return "label-id"

        def prune_gold_examples(self, *, tenant_id, split, keep_content_hashes, **_kw) -> int:
            self.pruned = (tenant_id, split, set(keep_content_hashes))
            return 0

    store = _RecordingStore()
    load_smoke_gold_set(store)
    assert store.pruned is not None, "loader must prune stale rows (authoritative load)"
    tenant, split, keep = store.pruned
    assert tenant == SMOKE_TENANT
    assert split == SMOKE_SPLIT
    # keep set is EXACTLY the current in-memory set — nothing more, nothing less.
    assert keep == {content_hash(e.input) for e in iter_smoke_examples()}


# ── Integration: real Postgres KB ─────────────────────────────────────────────

_pg = pytest.mark.skipif(
    not os.getenv("ENGINE_DATABASE_URL"),
    reason="requires Postgres (set ENGINE_DATABASE_URL)",
)


# These integration tests wipe+reseed the eval-KB tables on the fixed SMOKE_TENANT.
# The suite's Postgres is SHARED — other engine workers run the same smoke loader
# concurrently — and ``TRUNCATE gold_example`` is table-wide (NOT tenant-scoped, and
# the fixture role is a BYPASSRLS superuser so RLS can't gate it either), so a
# neighbour's truncate can delete an example row between our own
# ``upsert_gold_example`` and its dependent ``gold_label`` insert, surfacing as a
# ForeignKeyViolation. We isolate this module's eval-KB into a PRIVATE per-process
# schema (via ``search_path``): a concurrent ``TRUNCATE public.gold_example`` can no
# longer touch our rows, so the gold-set tests are deterministic regardless of what
# else is hitting the database (CustomerAcq-gel).
_ISO_SCHEMA = f"smoke_gel_{os.getpid()}_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module", autouse=True)
def _isolated_schema():
    """Create (and drop) a private schema so this module's eval-KB never collides
    with a concurrent worker's ``public`` gold tables on the shared Postgres."""
    import psycopg

    from tests.conftest import DB_DSN

    with psycopg.connect(DB_DSN, autocommit=True) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{_ISO_SCHEMA}" CASCADE')
        conn.execute(f'CREATE SCHEMA "{_ISO_SCHEMA}"')
    yield
    with psycopg.connect(DB_DSN, autocommit=True) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{_ISO_SCHEMA}" CASCADE')


@pytest.fixture
def dsn(_isolated_schema) -> str:
    """Point every eval-KB connection this module opens (fixture setup + the
    KbStore) at the private schema, so its tables are isolated from ``public``."""
    from tests.conftest import DB_DSN, bounded_dsn

    return bounded_dsn(DB_DSN, search_path=f"{_ISO_SCHEMA},public")


@pytest.fixture
def kb_store(dsn):
    """A clean eval-KB store, materialized inside the module's private schema so a
    concurrent worker's ``TRUNCATE public.gold_example`` cannot wipe our rows
    mid-test (CustomerAcq-gel). The reseed still runs through the normal
    tenant-scoped DAL, so tenant isolation stays exercised."""
    import psycopg

    from kb.store import KbStore
    from pathlib import Path

    schema = Path(__file__).resolve().parents[2] / "infra" / "initdb" / "03-eval-kb.sql"
    with psycopg.connect(dsn, autocommit=True) as conn:
        # search_path[0] is the private schema, so the eval-KB tables are created
        # and wiped THERE — isolated from any concurrent truncate on public.
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
def test_reload_prunes_a_stale_row(kb_store):
    """CustomerAcq-wwy.5: a SMOKE row no longer in the current set is pruned on the
    next load, so the persistent KB holds EXACTLY the current dataset (the honest
    set the gate scores) — never a superset that could false-fail."""
    load_smoke_gold_set(kb_store)
    assert sum(len(get_smoke_set(kb_store, e)) for e in _ENGINES) == len(iter_smoke_examples())

    # Seed a stale SMOKE row (an old-taxonomy example no longer in _ALL).
    stale_id = kb_store.upsert_gold_example(
        tenant_id=SMOKE_TENANT, engine=Engine.ENGAGEMENT, cell="triage",
        input={"kind": "engagement", "channel": "comment", "text": "STALE old-taxonomy row"},
        expected={"triage_class": "spam", "reply_safety": "safe-to-auto"},
        split=SMOKE_SPLIT,
    )
    assert any(r.id == stale_id for r in get_smoke_set(kb_store, Engine.ENGAGEMENT))

    # Re-load: authoritative -> stale row pruned, current set intact (no dup, no drift).
    load_smoke_gold_set(kb_store)
    eng_rows = get_smoke_set(kb_store, Engine.ENGAGEMENT)
    assert all(r.id != stale_id for r in eng_rows), "stale row must be pruned"
    assert sum(len(get_smoke_set(kb_store, e)) for e in _ENGINES) == len(iter_smoke_examples())


@pytest.mark.integration
@_pg
def test_prune_never_touches_holdout_or_other_tenants(kb_store):
    """The prune is SMOKE-split + tenant scoped: a real HOLDOUT row and another
    tenant's row both survive a smoke re-load."""
    load_smoke_gold_set(kb_store)
    holdout_id = kb_store.upsert_gold_example(
        tenant_id=SMOKE_TENANT, engine=Engine.POSTING, cell="copywriter",
        input={"kind": "caption", "text": "a real holdout caption"},
        expected={"on_voice": True}, split=Split.HOLDOUT,
    )
    other_id = kb_store.upsert_gold_example(
        tenant_id="real-client-xyz", engine=Engine.ENGAGEMENT, cell="triage",
        input={"kind": "engagement", "channel": "comment", "text": "another tenant's row"},
        expected={"triage_class": "positive", "reply_safety": "safe-to-auto"},
        split=SMOKE_SPLIT,
    )

    load_smoke_gold_set(kb_store)  # prune runs for SMOKE_TENANT / SMOKE split only

    holdout = kb_store.get_gold_set(tenant_id=SMOKE_TENANT, engine=Engine.POSTING, split=Split.HOLDOUT)
    assert any(r.id == holdout_id for r in holdout), "HOLDOUT row must survive the prune"
    other = kb_store.get_gold_set(tenant_id="real-client-xyz", engine=Engine.ENGAGEMENT, split=SMOKE_SPLIT)
    assert any(r.id == other_id for r in other), "another tenant's row must survive the prune"


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
