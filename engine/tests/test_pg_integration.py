"""Integration smoke — proves CI's real Postgres/pgvector wiring works.

Marked ``integration`` so it is EXCLUDED from the DB-free unit run (and the local
done-gate) and runs only in CI's pgvector-service job, where ``ENGINE_DATABASE_URL``
points at a live Postgres. This is the canary that keeps real-PG/async defects
from hiding under ``InMemorySaver``: the integration job must actually connect to
a database and exercise it.

Engine-level checkpointer + run-store integration tests (HARN-03 / dhv.6) join
this job by carrying the same ``@pytest.mark.integration`` marker.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

DB_URL = os.environ.get("ENGINE_DATABASE_URL")


@pytest.mark.skipif(not DB_URL, reason="ENGINE_DATABASE_URL not set (needs a live Postgres)")
def test_postgres_reachable_and_pgvector_available():
    """The configured Postgres is reachable and the pgvector extension works."""
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(DB_URL) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1

        # pgvector is the substrate the engine's KB + checkpoints rely on.
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        assert cur.fetchone() is not None, "pgvector extension not available"
