"""research_sources store (slice 3) — real Postgres persistence + honesty.

Runs against the local Postgres (skips cleanly if it is not reachable, like the
other DB-backed tests). Asserts the schema applies idempotently, real sources
persist with their query/url/title/snippet intact, and an empty source list
persists NOTHING (an honest-empty research run leaves no rows).
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from research import sources_store
from tests.conftest import DB_DSN


def _require_db():
    try:
        with psycopg.connect(DB_DSN, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"local Postgres not reachable at {DB_DSN} ({exc})")


def test_ensure_schema_is_idempotent():
    _require_db()
    sources_store.ensure_schema(DB_DSN)
    sources_store.ensure_schema(DB_DSN)  # second call must be a no-op, not an error


def test_record_and_list_real_sources():
    _require_db()
    sources_store.ensure_schema(DB_DSN)
    run_id = f"test-research-{uuid.uuid4().hex[:8]}"
    sources = [
        {"query": "fine line aftercare", "url": "https://studio-a.test/aftercare",
         "title": "Aftercare A", "snippet": "snip a"},
        {"query": "tattoo trends", "url": "https://studio-b.test/trends",
         "title": "Trends B", "snippet": None},
    ]
    ids = sources_store.record_sources(
        run_id=run_id, tenant_id="inkhaven", sources=sources, dsn=DB_DSN
    )
    assert len(ids) == 2
    rows = sources_store.list_sources(run_id, dsn=DB_DSN)
    assert {r["url"] for r in rows} == {
        "https://studio-a.test/aftercare", "https://studio-b.test/trends",
    }
    by_url = {r["url"]: r for r in rows}
    assert by_url["https://studio-a.test/aftercare"]["query"] == "fine line aftercare"
    assert by_url["https://studio-a.test/aftercare"]["title"] == "Aftercare A"
    assert by_url["https://studio-b.test/trends"]["snippet"] is None
    assert all(r["fetched_at"] is not None for r in rows)


def test_empty_sources_persists_nothing():
    _require_db()
    sources_store.ensure_schema(DB_DSN)
    run_id = f"test-research-empty-{uuid.uuid4().hex[:8]}"
    ids = sources_store.record_sources(
        run_id=run_id, tenant_id="inkhaven", sources=[], dsn=DB_DSN
    )
    assert ids == []
    assert sources_store.list_sources(run_id, dsn=DB_DSN) == []


def test_source_without_url_is_skipped():
    _require_db()
    sources_store.ensure_schema(DB_DSN)
    run_id = f"test-research-nourl-{uuid.uuid4().hex[:8]}"
    ids = sources_store.record_sources(
        run_id=run_id,
        tenant_id="inkhaven",
        sources=[{"query": "q", "url": "", "title": "no url", "snippet": "x"}],
        dsn=DB_DSN,
    )
    assert ids == []
    assert sources_store.list_sources(run_id, dsn=DB_DSN) == []
