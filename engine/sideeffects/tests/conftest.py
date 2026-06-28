"""Shared fixtures for side-effect-boundary tests.

These tests run against the REAL local Postgres (the infra/ docker stack) so the
UNIQUE constraints and row-locking that enforce exactly-once are actually
exercised — a mocked DB would prove nothing. Bring the stack up first:

    cd infra && docker compose up -d
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import psycopg
import pytest
import pytest_asyncio

# psycopg's async mode cannot run on Windows' default ProactorEventLoop; the
# SelectorEventLoop is required. Set it at import time so every test loop uses it.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

DSN = os.environ.get(
    "SCALERS_TEST_DSN", "postgresql://scalers:scalers@localhost:5432/scalers"
)

# infra/initdb/02-side-effect-boundary.sql — the schema under test.
_SCHEMA_SQL = (
    Path(__file__).resolve().parents[3]
    / "infra"
    / "initdb"
    / "02-side-effect-boundary.sql"
)


def _require_db() -> None:
    try:
        with psycopg.connect(DSN, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001 - surface a clear, actionable skip
        pytest.skip(
            f"local Postgres not reachable at {DSN} ({exc}). "
            "Start it with: cd infra && docker compose up -d",
            allow_module_level=True,
        )


def pytest_collection_modifyitems(config, items):
    # Fail loudly if someone removes the schema file; never silently pass.
    assert _SCHEMA_SQL.exists(), f"schema migration missing: {_SCHEMA_SQL}"


@pytest_asyncio.fixture
async def db():
    """A clean, schema-applied async connection; tables truncated per test."""
    _require_db()
    # Apply the migration (idempotent) and reset the two tables.
    schema = _SCHEMA_SQL.read_text(encoding="utf-8")
    async with await psycopg.AsyncConnection.connect(DSN, autocommit=True) as setup:
        await setup.execute(schema)
        await setup.execute("TRUNCATE side_effect_ledger, outbox RESTART IDENTITY")

    conn = await psycopg.AsyncConnection.connect(DSN, autocommit=False)
    try:
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture
def dsn() -> str:
    return DSN
