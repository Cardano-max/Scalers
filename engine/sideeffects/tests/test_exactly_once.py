"""The core safety guarantee: a side effect fires EXACTLY ONCE under
forced retries, concurrency, and crash-between-write-and-dispatch
(systemdesign §3 + §7, HARN-04).

All tests run against the real local Postgres so the UNIQUE constraints and
`FOR UPDATE SKIP LOCKED` claiming are genuinely exercised.
"""

from __future__ import annotations

import asyncio

import psycopg
import pytest

from engine.sideeffects import Channel, idempotency_key
from engine.sideeffects.boundary import EnqueueStatus, SideEffectBoundary
from engine.sideeffects.dispatcher import Dispatcher
from engine.sideeffects.tests.mock_connector import MockConnector

KEY_ARGS = ("nw", Channel.OUTREACH, "bayside-pg", "the one true message")


async def _outbox_rows(conn: psycopg.AsyncConnection, key: str):
    cur = await conn.execute(
        "SELECT status FROM outbox WHERE idempotency_key = %s", (key,)
    )
    return await cur.fetchall()


async def _ledger_rows(conn: psycopg.AsyncConnection, key: str):
    cur = await conn.execute(
        "SELECT status, provider_id FROM side_effect_ledger WHERE idempotency_key = %s",
        (key,),
    )
    return await cur.fetchall()


async def test_enqueue_is_idempotent_under_graph_retry(db):
    """A replayed graph node re-enqueues the SAME key; the UNIQUE constraint
    keeps it to a single outbox row (no second side effect queued)."""
    boundary = SideEffectBoundary()
    key = idempotency_key(*KEY_ARGS)

    # First node execution: advance state + enqueue in one tx, then commit.
    async with db.transaction():
        first = await boundary.enqueue(db, key, Channel.OUTREACH, {"text": "hi"})
    assert first.status is EnqueueStatus.ENQUEUED

    # Crash + replay: the node runs again and tries to enqueue the same effect.
    async with db.transaction():
        second = await boundary.enqueue(db, key, Channel.OUTREACH, {"text": "hi"})
    assert second.status is EnqueueStatus.DUPLICATE

    rows = await _outbox_rows(db, key)
    assert len(rows) == 1, "replay must not create a second outbox row"
    assert rows[0][0] == "PENDING"


async def test_dispatch_calls_connector_exactly_once(db, dsn):
    """Dispatching a PENDING row calls the connector once and records one
    ledger row; a redundant dispatch finds nothing and does not call again."""
    boundary = SideEffectBoundary()
    connector = MockConnector()
    dispatcher = Dispatcher(dsn, connector)
    key = idempotency_key(*KEY_ARGS)

    async with db.transaction():
        await boundary.enqueue(db, key, Channel.OUTREACH, {"text": "hi"})

    processed = await dispatcher.dispatch_pending()
    assert processed == 1
    assert connector.call_count == 1

    # Second dispatch: the row is SENT, nothing PENDING remains.
    processed_again = await dispatcher.dispatch_pending()
    assert processed_again == 0
    assert connector.call_count == 1

    assert [r[0] for r in await _outbox_rows(db, key)] == ["SENT"]
    ledger = await _ledger_rows(db, key)
    assert len(ledger) == 1
    assert ledger[0][0] == "SENT" and ledger[0][1] is not None


async def test_concurrent_enqueue_same_key_yields_one_row(db, dsn):
    """N runs racing to enqueue the same logical effect -> exactly one row."""
    boundary = SideEffectBoundary()
    key = idempotency_key(*KEY_ARGS)

    async def enqueue_once():
        conn = await psycopg.AsyncConnection.connect(dsn, autocommit=False)
        try:
            async with conn.transaction():
                return await boundary.enqueue(conn, key, Channel.OUTREACH, {"t": 1})
        finally:
            await conn.close()

    results = await asyncio.gather(*[enqueue_once() for _ in range(8)])
    enqueued = [r for r in results if r.status is EnqueueStatus.ENQUEUED]
    assert len(enqueued) == 1, "exactly one racer wins the insert"
    assert len(await _outbox_rows(db, key)) == 1


async def test_concurrent_dispatch_fires_connector_once(db, dsn):
    """N dispatchers draining the same single PENDING row -> one connector
    call total (FOR UPDATE SKIP LOCKED + ledger UNIQUE)."""
    boundary = SideEffectBoundary()
    connector = MockConnector()
    key = idempotency_key(*KEY_ARGS)

    async with db.transaction():
        await boundary.enqueue(db, key, Channel.OUTREACH, {"text": "hi"})

    dispatchers = [Dispatcher(dsn, connector) for _ in range(8)]
    await asyncio.gather(*[d.dispatch_pending() for d in dispatchers])

    assert connector.call_count == 1
    assert len(await _ledger_rows(db, key)) == 1
    assert [r[0] for r in await _outbox_rows(db, key)] == ["SENT"]


async def test_redelivery_does_not_recall_connector_when_ledger_records_effect(db, dsn):
    """The dispatcher's dedup gate: if the ledger ALREADY records the effect
    for this key (e.g. a keyed real connector recorded it, or a prior at-least-
    once attempt), recovery must NOT call the connector again — it only settles
    the outbox row. (§3: a unique-violation means "already done".) This exercises
    the `we_own_it == False` branch of the dispatcher."""
    boundary = SideEffectBoundary()
    connector = MockConnector()
    dispatcher = Dispatcher(dsn, connector)
    key = idempotency_key(*KEY_ARGS)

    async with db.transaction():
        await boundary.enqueue(db, key, Channel.OUTREACH, {"text": "hi"})

    # Simulate the first attempt having already recorded the effect in the
    # ledger, then crashing before settling the outbox row (still PENDING).
    async with db.transaction():
        await db.execute(
            "INSERT INTO side_effect_ledger (idempotency_key, channel, provider_id, status)"
            " VALUES (%s, %s, %s, 'SENT')",
            (key, "outreach", "prov-prior"),
        )

    processed = await dispatcher.dispatch_pending()
    assert processed == 1
    assert connector.call_count == 0, "ledger says done -> connector must not run"
    assert [r[0] for r in await _outbox_rows(db, key)] == ["SENT"]
    assert len(await _ledger_rows(db, key)) == 1
