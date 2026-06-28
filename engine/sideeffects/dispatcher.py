"""The at-least-once dispatcher that drains the outbox (HARN-04).

For each PENDING outbox row it does, in one transaction holding the row lock:

  1. claim a PENDING row with ``FOR UPDATE SKIP LOCKED`` (so concurrent
     dispatchers never grab the same row);
  2. try to record the effect in `side_effect_ledger` (UNIQUE key). The insert
     is the dedup gate:
       - inserted  -> WE own this send: call the connector exactly once, then
         settle the ledger row with the provider id;
       - conflict  -> the effect was ALREADY recorded (a prior attempt that
         crashed before settling the outbox): do NOT call the connector again;
  3. flip the outbox row to SENT.

Because the ledger insert precedes the connector call and is unique-constrained,
at-least-once dispatch becomes effectively exactly-once: the connector is never
called twice for the same key. The connector is only ever reached here, only for
a claimed outbox row.
"""

from __future__ import annotations

import psycopg


class Dispatcher:
    def __init__(self, dsn: str, connector) -> None:
        self._dsn = dsn
        self._connector = connector

    async def dispatch_pending(self) -> int:
        """Drain all currently-PENDING rows. Returns the number settled."""
        processed = 0
        conn = await psycopg.AsyncConnection.connect(self._dsn, autocommit=False)
        try:
            while await self._dispatch_one(conn):
                processed += 1
        finally:
            await conn.close()
        return processed

    async def _dispatch_one(self, conn: psycopg.AsyncConnection) -> bool:
        async with conn.transaction():
            cur = await conn.execute(
                "SELECT id, idempotency_key, channel, payload FROM outbox"
                " WHERE status = 'PENDING'"
                " ORDER BY id"
                " FOR UPDATE SKIP LOCKED"
                " LIMIT 1"
            )
            row = await cur.fetchone()
            if row is None:
                return False
            outbox_id, key, channel, payload = row

            # Dedup gate: claim the ledger row BEFORE touching the connector.
            claim = await conn.execute(
                "INSERT INTO side_effect_ledger (idempotency_key, channel, status)"
                " VALUES (%s, %s, 'SENDING')"
                " ON CONFLICT (idempotency_key) DO NOTHING"
                " RETURNING id",
                (key, channel),
            )
            we_own_it = await claim.fetchone() is not None

            if we_own_it:
                provider_id = await self._connector.send(key, channel, payload)
                await conn.execute(
                    "UPDATE side_effect_ledger"
                    " SET status = 'SENT', provider_id = %s"
                    " WHERE idempotency_key = %s",
                    (provider_id, key),
                )

            await conn.execute(
                "UPDATE outbox SET status = 'SENT', updated_at = now() WHERE id = %s",
                (outbox_id,),
            )
            return True
