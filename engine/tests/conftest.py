"""Shared test helpers: deterministic model injection.

Cells never call a real LLM in tests. Instead we drive them with Pydantic-AI's
``FunctionModel``, scripting exactly what the "model" returns on each call so we
can exercise valid output, repair-on-retry, persistent failure, and messy text.
"""

from __future__ import annotations

import asyncio
import itertools
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
import pytest
import pytest_asyncio
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

# psycopg's async mode cannot run on Windows' default ProactorEventLoop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

DB_DSN = (
    os.environ.get("ENGINE_DATABASE_URL")
    or os.environ.get("SCALERS_TEST_DSN")
    or "postgresql://scalers:scalers@localhost:5432/scalers"
)

_INITDB = Path(__file__).resolve().parents[2] / "infra" / "initdb"
# The side-effect schema chain: the exactly-once boundary (02) + the OBS-03
# deep-link/engagement capture columns (05). Applied in order.
_SCHEMA_SQLS = [_INITDB / "02-side-effect-boundary.sql", _INITDB / "05-side-effect-capture.sql"]
_SCHEMA_SQL = _SCHEMA_SQLS[0]  # back-compat alias


def bounded_dsn(
    dsn: str = DB_DSN,
    *,
    lock_ms: int | None = 10_000,
    stmt_ms: int | None = None,
    search_path: str | None = None,
) -> str:
    """A DSN carrying server-side ``options`` for test isolation/robustness.

    ``lock_ms`` / ``stmt_ms`` cap every connection's lock (and optional statement)
    wait, so a pathological lock-wait under full-suite pressure fails FAST and LOUD
    — a raised ``LockNotAvailable`` / ``QueryCanceled`` — instead of hanging the run
    indefinitely (CustomerAcq-b4q). The exactly-once dispatcher already prevents a
    true deadlock via a consistent outbox→ledger lock order; this is the backstop
    for lock-wait starvation among the racing dispatchers and for a stuck lock left
    on a shared table by a neighbour.

    ``search_path`` pins the connection to a PRIVATE schema so a module's tables are
    isolated from ``public`` on the SHARED test Postgres — a concurrent worker's
    table-wide ``TRUNCATE`` on ``public`` can no longer delete our rows mid-test
    (CustomerAcq-gel)."""
    from urllib.parse import quote

    opts: list[str] = []
    if search_path is not None:
        opts.append(f"-c search_path={search_path}")
    if lock_ms is not None:
        opts.append(f"-c lock_timeout={lock_ms}")
    if stmt_ms is not None:
        opts.append(f"-c statement_timeout={stmt_ms}")
    if not opts:
        return dsn
    sep = "&" if "?" in dsn else "?"
    return f"{dsn}{sep}options={quote(' '.join(opts))}"


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
    """Apply the outbox/ledger schema chain + truncate (sync, for non-async setups)."""
    with psycopg.connect(DB_DSN, autocommit=True) as conn:
        for sql in _SCHEMA_SQLS:
            conn.execute(sql.read_text(encoding="utf-8"))
        conn.execute("TRUNCATE side_effect_ledger, outbox RESTART IDENTITY")


@pytest_asyncio.fixture
async def db():
    """A clean, schema-applied async connection; tables truncated per test.

    Both connections cap their lock wait (:func:`bounded_dsn`) so the setup
    ``TRUNCATE`` (which needs ACCESS EXCLUSIVE) fails fast if a neighbouring test
    leaked a conflicting lock, rather than hanging the whole run (CustomerAcq-b4q).
    """
    _require_db()
    async with await psycopg.AsyncConnection.connect(bounded_dsn(), autocommit=True) as setup:
        for sql in _SCHEMA_SQLS:
            assert sql.exists(), f"schema migration missing: {sql}"
            await setup.execute(sql.read_text(encoding="utf-8"))
        await setup.execute("TRUNCATE side_effect_ledger, outbox RESTART IDENTITY")

    conn = await psycopg.AsyncConnection.connect(bounded_dsn(), autocommit=False)
    try:
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture
def dsn() -> str:
    return DB_DSN


# ── per-process private test schema (fr1.3, AC-4) ────────────────────────────
# The proven fix for shared-Postgres cross-process pollution (the audit's
# junk-row incident + the gold/eval flake family): each caller gets its OWN
# schema, and the schema is baked into the DSN via ``options=-c search_path=``
# so EVERY connection made with that DSN — including the ones the ledger/ops
# code opens internally — reads and writes only that schema. Two processes (or
# two `with` blocks) cannot see or TRUNCATE each other's rows.
#
# Scope note: by default the private schema is private-ONLY on the search_path
# (no ``public``). Suites whose DDL needs objects installed in ``public`` —
# pgvector's ``vector`` type / ``vector_cosine_ops`` opclass — pass
# ``include_public=True``, which appends ``public`` AFTER the private schema.
# ``CREATE TABLE IF NOT EXISTS`` still creates into the private schema even
# when ``public`` holds a same-named live table (verified empirically on the
# shared cluster: the IF-NOT-EXISTS check is against the creation-target
# schema, i.e. the FIRST entry of the search_path, not the whole path), and
# unqualified reads/writes resolve to the private copy first — so the live
# ``public`` tables stay untouched (CustomerAcq-wwy.9).

_schema_counter = itertools.count()


def _dsn_with_search_path(base_dsn: str, search_path: str) -> str:
    """Return ``base_dsn`` with libpq ``options=-c search_path=<search_path>``
    set, so every connection opened from it lands in the private schema."""
    parts = urlsplit(base_dsn)
    opt = f"-c search_path={search_path}"
    q = f"options={quote(opt)}"
    query = f"{parts.query}&{q}" if parts.query else q
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


@contextmanager
def private_schema(*initdb_names: str, include_public: bool = False):
    """Context manager yielding ``SimpleNamespace(dsn, schema)`` bound to a
    fresh per-process private schema. ``initdb_names`` are ``infra/initdb``
    SQL files applied INTO that schema (unqualified DDL lands there because the
    private schema is FIRST on the search_path). ``include_public=True`` keeps
    ``public`` on the path AFTER the private schema so extension objects
    (pgvector) resolve; tables are still created/read privately (see scope note
    above). The schema is dropped CASCADE on exit."""
    _require_db()
    schema = f"test_p{os.getpid()}_{next(_schema_counter)}"
    path = f'"{schema}", public' if include_public else f'"{schema}"'
    dsn_path = f"{schema},public" if include_public else schema
    with psycopg.connect(DB_DSN, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        conn.execute(f"SET search_path TO {path}")
        for name in initdb_names:
            conn.execute((_INITDB / name).read_text(encoding="utf-8"))
        if include_public:
            # RLS suites connect as the non-superuser scalers_app; it needs
            # USAGE on the private schema to resolve the tables the initdb SQL
            # just created (and granted) there. Best-effort, like the SQL files.
            conn.execute(
                f"""
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'scalers_app') THEN
                        GRANT USAGE ON SCHEMA "{schema}" TO scalers_app;
                    END IF;
                EXCEPTION WHEN insufficient_privilege THEN
                    NULL;
                END $$;
                """
            )
    try:
        yield SimpleNamespace(dsn=_dsn_with_search_path(DB_DSN, dsn_path), schema=schema)
    finally:
        with psycopg.connect(DB_DSN, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
