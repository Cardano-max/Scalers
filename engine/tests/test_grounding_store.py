"""GLOBAL practitioner-wisdom grounding store — real Postgres+pgvector (1mk.9).

Covers the bead's acceptance: verbatim preservation through the DB round-trip
(no paraphrase), idempotent re-ingest (no dups), category-filtered retrieval,
GLOBAL visibility (no tenant scoping), and empty-partition cleanliness.

Marked ``integration`` + ``skipif(ENGINE_DATABASE_URL)`` (rvy.2 / PR convention).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
import pytest

from kb import GroundingStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

_INITDB = Path(__file__).resolve().parents[2] / "infra" / "initdb"
_EVAL_SCHEMA = _INITDB / "03-eval-kb.sql"          # rvy.2 — provides the role + extension
_GROUNDING_SCHEMA = _INITDB / "04-grounding-kb.sql"
_JSONL = Path(__file__).resolve().parents[1] / "kb" / "corpus" / "practitioner_wisdom.jsonl"


@pytest.fixture
def grounding(dsn) -> GroundingStore:
    """Apply the grounding schema (idempotent) + reset the table."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(_EVAL_SCHEMA.read_text(encoding="utf-8"))
        conn.execute(_GROUNDING_SCHEMA.read_text(encoding="utf-8"))
        conn.execute("TRUNCATE practitioner_wisdom")
    return GroundingStore(dsn)


def _entries() -> list[dict]:
    return [json.loads(line) for line in _JSONL.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── verbatim round-trip ──────────────────────────────────────────────────────


def test_text_survives_db_round_trip_verbatim(grounding):
    """The asset: a sentence loaded then read back is byte-identical — the DB
    layer never normalizes or paraphrases."""
    quote = 'cleaning up client comunication so it sounds human instead of corporate sludge'  # original typo kept
    chash = __import__("hashlib").sha256(quote.encode()).hexdigest()
    grounding.upsert(
        text=quote, category="brand-voice", kind="testimonial",
        source={"author": "Winter-Picture8807", "thread": "T1"},
        content_hash=chash, harvested_at="2026-06-28",
    )
    [row] = grounding.list(category="brand-voice")
    assert row.text == quote  # exact, including "comunication"


def test_full_harvest_loads_and_preserves_every_sentence(grounding):
    entries = _entries()
    loaded = grounding.load_entries(entries)
    assert loaded == len(entries)
    assert grounding.count() == len(entries)
    # Spot-check three exact sentences (incl. embedded quote + non-ascii).
    by_hash = {r.content_hash: r.text for r in grounding.list()}
    for e in entries:
        assert by_hash[e["content_hash"]] == e["text"]


# ── idempotency ──────────────────────────────────────────────────────────────


def test_reingest_is_idempotent_no_dups(grounding):
    entries = _entries()
    grounding.load_entries(entries)
    first = grounding.count()
    grounding.load_entries(entries)  # load the same harvest again
    assert grounding.count() == first  # natural key (partition, content_hash)


# ── retrieval (the grounding path S2/S5 call) ────────────────────────────────


def test_retrieve_filters_by_category(grounding):
    grounding.load_entries(_entries())
    hits = grounding.retrieve("how do I sound human and avoid AI tells", category="brand-voice", k=5)
    assert hits and all(h.category == "brand-voice" for h in hits)
    assert all(h.distance is not None for h in hits)


def test_retrieve_is_global_no_tenant_needed(grounding):
    """No tenant set anywhere — global wisdom is retrievable by construction."""
    grounding.load_entries(_entries())
    hits = grounding.retrieve("competitor research and market mapping", k=3)
    assert len(hits) == 3


def test_empty_partition_returns_empty_not_error(grounding):
    assert grounding.list() == []
    assert grounding.retrieve("anything", k=5) == []
    assert grounding.count() == 0


# ── schema guards ────────────────────────────────────────────────────────────


def test_bad_category_rejected(grounding):
    with pytest.raises(ValueError):
        grounding.upsert(text="x", category="not-a-cat", content_hash="h")


def test_bad_kind_rejected(grounding):
    with pytest.raises(ValueError):
        grounding.upsert(text="x", category="general", kind="nope", content_hash="h")
