"""Shared test helpers: deterministic model injection.

Cells never call a real LLM in tests. Instead we drive them with Pydantic-AI's
``FunctionModel``, scripting exactly what the "model" returns on each call so we
can exercise valid output, repair-on-retry, persistent failure, and messy text.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel


def tool_model(*payloads: dict[str, Any]) -> FunctionModel:
    """A model that returns each ``payload`` as an output-tool call, in order.

    Use for structured cells. The Nth call returns ``payloads[N]``; once the
    payloads run out it repeats the last one (so a single bad payload models a
    persistently-broken model).
    """
    calls = {"n": 0}

    def fn(messages, info: AgentInfo) -> ModelResponse:
        idx = min(calls["n"], len(payloads) - 1)
        calls["n"] += 1
        tool_name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool_name, payloads[idx])])

    return FunctionModel(fn)


def text_model(*texts: str) -> FunctionModel:
    """A model that returns each raw ``text`` string, in order (for text-output cells)."""
    calls = {"n": 0}

    def fn(messages, info: AgentInfo) -> ModelResponse:
        idx = min(calls["n"], len(texts) - 1)
        calls["n"] += 1
        return ModelResponse(parts=[TextPart(texts[idx])])

    return FunctionModel(fn)


def error_model(exc: BaseException) -> FunctionModel:
    """A model that raises ``exc`` instead of responding.

    Simulates a non-ModelBehavior failure (network/timeout/connector/etc.) at the
    model boundary so the cell wrapper's error handling can be exercised.
    """

    def fn(messages, info: AgentInfo) -> ModelResponse:
        raise exc

    return FunctionModel(fn)


# A well-formed content brief payload reused across tests.
VALID_BRIEF: dict[str, Any] = {
    "headline": "Bold blackwork sleeve drop",
    "platform": "instagram",
    "angle": "Show the linework process to build trust with new clients",
    "caption": (
        "Three sessions, one sleeve. Swipe to watch the linework come together "
        "and book your chair for spring before the calendar fills."
    ),
    "hashtags": ["blackwork", "tattoo", "linework"],
    "call_to_action": "Book your spring chair",
}


# ── Side-effect-boundary + integration DB fixtures (HARN-04 / HARN-INT) ───────
# Run against the REAL local Postgres so UNIQUE constraints and row-locking are
# genuinely exercised. DSN resolves from ENGINE_DATABASE_URL (the value CI sets,
# and what the harness checkpointer reads) first, then SCALERS_TEST_DSN, then a
# local default. Bring the stack up:  cd infra && docker compose up -d
import asyncio
import os
import sys
from pathlib import Path

import psycopg
import pytest
import pytest_asyncio

# psycopg's async mode cannot run on Windows' default ProactorEventLoop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

DB_DSN = (
    os.environ.get("ENGINE_DATABASE_URL")
    or os.environ.get("SCALERS_TEST_DSN")
    or "postgresql://scalers:scalers@localhost:5432/scalers"
)

_SCHEMA_SQL = (
    Path(__file__).resolve().parents[2] / "infra" / "initdb" / "02-side-effect-boundary.sql"
)


def _require_db() -> None:
    try:
        with psycopg.connect(DB_DSN, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(
            f"local Postgres not reachable at {DB_DSN} ({exc}). "
            "Start it with: cd infra && docker compose up -d",
            allow_module_level=True,
        )


def apply_side_effect_schema_sync() -> None:
    """Apply the outbox/ledger schema + truncate (sync, for non-async setups)."""
    schema = _SCHEMA_SQL.read_text(encoding="utf-8")
    with psycopg.connect(DB_DSN, autocommit=True) as conn:
        conn.execute(schema)
        conn.execute("TRUNCATE side_effect_ledger, outbox RESTART IDENTITY")


@pytest_asyncio.fixture
async def db():
    """A clean, schema-applied async connection; tables truncated per test."""
    _require_db()
    assert _SCHEMA_SQL.exists(), f"schema migration missing: {_SCHEMA_SQL}"
    schema = _SCHEMA_SQL.read_text(encoding="utf-8")
    async with await psycopg.AsyncConnection.connect(DB_DSN, autocommit=True) as setup:
        await setup.execute(schema)
        await setup.execute("TRUNCATE side_effect_ledger, outbox RESTART IDENTITY")

    conn = await psycopg.AsyncConnection.connect(DB_DSN, autocommit=False)
    try:
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture
def dsn() -> str:
    return DB_DSN
