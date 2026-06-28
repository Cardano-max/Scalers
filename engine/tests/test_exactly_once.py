"""The core safety guarantee: a side effect produces EXACTLY ONE external
effect under forced retries, concurrency, and crash-between-send-and-commit
(systemdesign §3 + §7, HARN-04).

Exactly-once for a non-transactional external effect is impossible without an
idempotent consumer (the two-generals problem). The guarantee is therefore:

* the connector is keyed/idempotent (modeled by :class:`MockConnector`), and
* the dispatcher commits a DURABLE ``SENDING`` claim BEFORE calling it, so a
  crash after the effect never re-acquires ownership and the connector dedupes
  any redundant retry.

All tests run against the real local Postgres so the UNIQUE constraints and
`FOR UPDATE SKIP LOCKED` claiming are genuinely exercised.
"""

from __future__ import annotations

import asyncio

import psycopg
import pytest

from sideeffects import Channel, idempotency_key
from sideeffects.boundary import EnqueueStatus, SideEffectBoundary
from sideeffects.dispatcher import Dispatcher
from tests.mock_connector import MockConnector

# These exercise the exactly-once guarantee against a REAL Postgres (UNIQUE
# constraints, row locking, crash recovery), so they carry the `integration`
# marker (dhv.5 / PR #2 convention): excluded from the DB-free unit run, run in
# CI's pgvector-service job where ENGINE_DATABASE_URL is set. Without it these
# tests were deselected in the integration job AND skipped in the unit job — the
# guarantee never actually ran in CI (CustomerAcq-dh4).
pytestmark = pytest.mark.integration

KEY_ARGS = ("nw", Channel.OUTREACH, "bayside-pg", "the one true message")


async def _outbox_rows(conn: psycopg.AsyncConnection, key: str):
    cur = await conn.execute(
        "SELECT status FROM outbox WHERE idempotency_key = %s", (key,)
    )
    return await cur.fetchall()


async def _outbox_one(conn: psycopg.AsyncConnection, key: str):
    cur = await conn.execute(
        "SELECT status, attempts, last_error FROM outbox WHERE idempotency_key = %s",
        (key,),
    )
    return await cur.fetchone()


async def _ledger_rows(conn: psycopg.AsyncConnection, key: str):
    cur = await conn.execute(
        "SELECT status, provider_id FROM side_effect_ledger WHERE idempotency_key = %s",
        (key,),
    )
    return await cur.fetchall()


async def _enqueue(db, key, payload=None):
    async with db.transaction():
        await SideEffectBoundary().enqueue(
            db, key, Channel.OUTREACH, payload or {"text": "hi"}
        )


# ── Enqueue idempotency (unchanged behaviour) ────────────────────────────────


async def test_enqueue_is_idempotent_under_graph_retry(db):
    """A replayed graph node re-enqueues the SAME key; the UNIQUE constraint
    keeps it to a single outbox row (no second side effect queued)."""
    boundary = SideEffectBoundary()
    key = idempotency_key(*KEY_ARGS)

    async with db.transaction():
        first = await boundary.enqueue(db, key, Channel.OUTREACH, {"text": "hi"})
    assert first.status is EnqueueStatus.ENQUEUED

    async with db.transaction():
        second = await boundary.enqueue(db, key, Channel.OUTREACH, {"text": "hi"})
    assert second.status is EnqueueStatus.DUPLICATE

    rows = await _outbox_rows(db, key)
    assert len(rows) == 1, "replay must not create a second outbox row"
    assert rows[0][0] == "PENDING"


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


# ── Happy path ───────────────────────────────────────────────────────────────


async def test_dispatch_produces_exactly_one_effect(db, dsn):
    """Dispatching a PENDING row produces one effect + one ledger row; a
    redundant dispatch finds nothing SENDING/PENDING and does not invoke again."""
    connector = MockConnector()
    dispatcher = Dispatcher(dsn, connector)
    key = idempotency_key(*KEY_ARGS)
    await _enqueue(db, key)

    processed = await dispatcher.dispatch_pending()
    assert processed == 1
    assert connector.call_count == 1
    assert connector.invocation_count == 1

    processed_again = await dispatcher.dispatch_pending()
    assert processed_again == 0
    assert connector.call_count == 1

    assert [r[0] for r in await _outbox_rows(db, key)] == ["SENT"]
    ledger = await _ledger_rows(db, key)
    assert len(ledger) == 1
    assert ledger[0][0] == "SENT" and ledger[0][1] is not None


async def test_concurrent_dispatch_produces_one_effect(db, dsn):
    """N dispatchers draining the same row -> exactly ONE external effect.
    (Redundant invocations are allowed and deduped by the keyed connector.)"""
    connector = MockConnector()
    key = idempotency_key(*KEY_ARGS)
    await _enqueue(db, key)

    dispatchers = [Dispatcher(dsn, connector) for _ in range(8)]
    await asyncio.gather(*[d.dispatch_pending() for d in dispatchers])

    assert connector.call_count == 1, "exactly one external effect"
    assert len(await _ledger_rows(db, key)) == 1
    assert [r[0] for r in await _outbox_rows(db, key)] == ["SENT"]


# ── The bug QA found: crash AFTER send, BEFORE commit ────────────────────────


async def test_send_failure_preserves_durable_claim(db, dsn):
    """REGRESSION (qa2 cases b/e): a failure AFTER the connector performs its
    effect must leave a DURABLE 'SENDING' ledger claim. The old single-tx
    dispatcher rolled the claim back, so recovery re-acquired ownership and
    re-fired. The claim must survive."""
    key = idempotency_key(*KEY_ARGS)
    await _enqueue(db, key)

    # Connector performs the effect then crashes before our settle commits.
    connector = MockConnector(crash_on_first=True)
    await Dispatcher(dsn, connector).dispatch_pending()  # caught per-row, not raised

    assert connector.call_count == 1  # the effect happened exactly once
    rows = await _ledger_rows(db, key)
    assert len(rows) == 1, "the SENDING claim must survive a post-send failure"
    assert rows[0][0] == "SENDING"


async def test_crash_after_send_recovers_without_double_effect(db, dsn):
    """The headline crash-injection test. A crash fires the effect then dies
    before commit; a fresh dispatcher recovers. The external effect must happen
    EXACTLY ONCE (call_count==1) even though the connector is invoked twice and
    the provider dedupes the second."""
    key = idempotency_key(*KEY_ARGS)
    await _enqueue(db, key)
    connector = MockConnector(crash_on_first=True)

    # Attempt 1: effect fires, crash before settle -> row re-queued for retry.
    await Dispatcher(dsn, connector).dispatch_pending()
    assert connector.call_count == 1
    assert connector.invocation_count == 1

    # Attempt 2 (fresh dispatcher): idempotent re-drive completes the send.
    await Dispatcher(dsn, connector).dispatch_pending()

    assert connector.call_count == 1, "EXACTLY one external effect across the crash"
    assert connector.invocation_count == 2, "re-driven once; provider deduped it"
    assert [r[0] for r in await _ledger_rows(db, key)] == ["SENT"]
    assert [r[0] for r in await _outbox_rows(db, key)] == ["SENT"]


async def test_recovery_of_stuck_sending_claim_after_hard_crash(db, dsn):
    """Models a HARD process kill between send() and the settle commit (no
    exception handler runs): the durable claim (ledger SENDING + outbox SENDING)
    is committed and the effect already fired once. A fresh dispatcher recovers
    via the idempotent connector — still exactly ONE effect, row settles SENT."""
    key = idempotency_key(*KEY_ARGS)
    await _enqueue(db, key)
    connector = MockConnector()

    # The external effect already happened once before the crash.
    await connector.send(key, "outreach", {})
    # The durable state a hard-killed attempt leaves behind (both committed in
    # the dispatcher's Phase A, before the connector call).
    async with db.transaction():
        await db.execute(
            "INSERT INTO side_effect_ledger (idempotency_key, channel, status)"
            " VALUES (%s, 'outreach', 'SENDING')",
            (key,),
        )
        await db.execute(
            "UPDATE outbox SET status = 'SENDING' WHERE idempotency_key = %s", (key,)
        )

    await Dispatcher(dsn, connector).dispatch_pending()

    assert connector.call_count == 1, "still exactly one external effect"
    assert connector.invocation_count == 2, "re-driven once; provider deduped"
    assert [r[0] for r in await _outbox_rows(db, key)] == ["SENT"]
    assert [r[0] for r in await _ledger_rows(db, key)] == ["SENT"]


async def test_already_sent_ledger_skips_connector(db, dsn):
    """If the ledger already records a completed effect (status SENT + provider),
    recovery settles the outbox WITHOUT invoking the connector at all."""
    connector = MockConnector()
    key = idempotency_key(*KEY_ARGS)
    await _enqueue(db, key)

    async with db.transaction():
        await db.execute(
            "INSERT INTO side_effect_ledger (idempotency_key, channel, provider_id, status)"
            " VALUES (%s, %s, %s, 'SENT')",
            (key, "outreach", "prov-prior"),
        )

    processed = await Dispatcher(dsn, connector).dispatch_pending()
    assert processed == 1
    assert connector.invocation_count == 0, "already SENT -> never invoke the connector"
    assert [r[0] for r in await _outbox_rows(db, key)] == ["SENT"]
    assert len(await _ledger_rows(db, key)) == 1


# ── Idempotent-connector contract (the basis of the guarantee) ───────────────


async def test_idempotent_connector_contract():
    """The mock models a keyed provider: two sends with the same key produce one
    effect and the same provider_id. This is the contract the dispatcher relies
    on for the ambiguous recovery window."""
    connector = MockConnector()
    p1 = await connector.send("k", "outreach", {})
    p2 = await connector.send("k", "outreach", {})
    assert p1 == p2
    assert connector.call_count == 1
    assert connector.invocation_count == 2


# ── Poison pill: one bad row must not block the drain ────────────────────────


async def test_poison_row_does_not_block_other_rows(db, dsn):
    """A row whose connector always fails must not abort the drain — a healthy
    row enqueued alongside it still gets sent (per-row error isolation)."""
    good = idempotency_key("nw", Channel.OUTREACH, "good", "ok")
    bad = idempotency_key("nw", Channel.OUTREACH, "bad", "boom")
    await _enqueue(db, good)
    await _enqueue(db, bad)

    connector = MockConnector(poison_keys={bad})
    await Dispatcher(dsn, connector).dispatch_pending()

    assert [r[0] for r in await _outbox_rows(db, good)] == ["SENT"]
    bad_status, bad_attempts, bad_err = await _outbox_one(db, bad)
    assert bad_status in ("PENDING", "FAILED"), "bad row retried/failed, not lost"
    assert bad_attempts >= 1 and bad_err is not None


async def test_failing_row_goes_failed_after_max_attempts(db, dsn):
    """A persistently-failing row stops retrying once attempts hit the cap, so a
    dead row drops out of the drain instead of looping forever. The ledger claim
    is marked FAILED too, so it never strands a permanent SENDING/NULL row."""
    bad = idempotency_key("nw", Channel.OUTREACH, "bad", "boom")
    await _enqueue(db, bad)
    connector = MockConnector(poison_keys={bad})
    dispatcher = Dispatcher(dsn, connector, max_attempts=2)

    # Each drain attempts the row at most once; run until it gives up.
    for _ in range(5):
        await dispatcher.dispatch_pending()

    status, attempts, err = await _outbox_one(db, bad)
    assert status == "FAILED"
    assert attempts == 2
    assert err is not None
    # No phantom claim left behind: the ledger row reflects the give-up.
    ledger = await _ledger_rows(db, bad)
    assert len(ledger) == 1 and ledger[0][0] == "FAILED"


async def test_max_attempts_below_two_is_rejected():
    """max_attempts=1 leaves no headroom to settle a crash-after-send, so it is
    rejected at construction rather than silently losing effects."""
    with pytest.raises(ValueError):
        Dispatcher("postgresql://x/y", MockConnector(), max_attempts=1)


async def test_many_concurrent_dispatchers_no_deadlock_one_effect(db, dsn):
    """16 dispatchers draining the same row must NOT deadlock (consistent
    outbox->ledger lock ordering) and must produce exactly one effect with the
    row settled SENT. Regression for the lock-order inversion the audit found."""
    connector = MockConnector()
    key = idempotency_key(*KEY_ARGS)
    await _enqueue(db, key)

    dispatchers = [Dispatcher(dsn, connector) for _ in range(16)]
    # gather without return_exceptions: a deadlock would surface here and fail.
    await asyncio.gather(*[d.dispatch_pending() for d in dispatchers])

    assert connector.call_count == 1
    assert [r[0] for r in await _outbox_rows(db, key)] == ["SENT"]
    assert [r[0] for r in await _ledger_rows(db, key)] == ["SENT"]
