"""The at-least-once dispatcher that drains the outbox (HARN-04).

**Honest guarantee.** Exactly-once for a non-transactional external effect (an IG
post, a Gmail send) is impossible without an idempotent consumer — this is the
two-generals problem. So the guarantee is built from two parts:

* the connector is **keyed/idempotent**: calling ``send`` twice with the same
  idempotency key performs the effect once (real IG/Gmail provide this via an
  idempotency token; the Phase-1 mock models it), and
* the dispatcher commits a **durable claim BEFORE the external call**, so a crash
  after the effect can never roll the claim back and re-acquire ownership.

Per row, the dispatcher runs three SEPARATE committed transactions around the
external call — never one tx spanning ``send`` (that was the bug: a rollback
after ``send`` undid the dedup row and the retry double-fired):

  A. **Claim** (own tx, committed): insert a ``SENDING`` ledger row
     (``ON CONFLICT DO NOTHING``) and mark the outbox row ``SENDING``. The ledger
     row is the durable dedup record; it survives any later failure.
  B. **Send**: call the connector (skipped if the ledger already says ``SENT``).
  C. **Settle** (own tx, committed): ledger -> ``SENT`` + ``provider_id``, outbox
     -> ``SENT``.

On a send failure the row is re-queued (``PENDING``) with an incremented
``attempts`` and ``last_error``, or moved to ``FAILED`` past ``max_attempts``.
Failures are caught **per row** so one poison row never aborts the drain. A crash
between B and C leaves a committed ``SENDING`` claim with a NULL ``provider_id``
(ambiguous: the effect may have fired); recovery re-drives through the idempotent
connector, which dedupes, so the effect still happens exactly once.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import psycopg

# Must be >= 2: a crash-after-send is recovered by a SUBSEQUENT drain, which
# consumes one attempt. With max_attempts=1 a single crash-after-effect would be
# marked FAILED with no chance to settle. 5 gives comfortable headroom.
DEFAULT_MAX_ATTEMPTS = 5


@runtime_checkable
class Connector(Protocol):
    """A side-effect connector (real Meta/Gmail MCP in Phase 6; a mock in Phase 1).

    CONTRACT — the connector MUST be idempotent on ``key``: calling ``send`` more
    than once with the same idempotency key performs the external effect exactly
    once and returns the same provider id. This is non-negotiable; it is the only
    way exactly-once holds across a crash in the send→commit window. Real APIs
    implement it with an idempotency token derived from ``key``.
    """

    async def send(self, key: str, channel: str, payload: dict) -> str:
        """Perform the effect (idempotently) and return the provider id."""
        ...


class Dispatcher:
    def __init__(self, dsn: str, connector, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> None:
        if max_attempts < 2:
            raise ValueError(
                "max_attempts must be >= 2 so a crash-after-send can be settled by"
                f" a later drain; got {max_attempts!r}"
            )
        self._dsn = dsn
        self._connector = connector
        self._max_attempts = max_attempts

    async def dispatch_pending(self) -> int:
        """Drain every currently-eligible row once. Returns the number SETTLED.

        A per-drain ``seen`` set guarantees each row is attempted at most once per
        call, so a row re-queued after a failure waits for the next drain instead
        of spinning in this one.
        """
        settled = 0
        seen: list[int] = []
        conn = await psycopg.AsyncConnection.connect(self._dsn, autocommit=False)
        try:
            while True:
                try:
                    claimed = await self._claim_next(conn, seen)
                    if claimed is None:
                        break
                    outbox_id, key, channel, payload, ledger_status, provider_id = claimed
                    seen.append(outbox_id)
                    if await self._send_and_settle(
                        conn, outbox_id, key, channel, payload, ledger_status, provider_id
                    ):
                        settled += 1
                except (
                    psycopg.errors.DeadlockDetected,
                    psycopg.errors.SerializationFailure,
                ):
                    # Consistent outbox->ledger lock ordering makes this
                    # unreachable in practice; if a contended interleaving still
                    # trips it, the victim tx is already rolled back. End this
                    # drain cleanly and let the next one retry the row.
                    break
        finally:
            await conn.close()
        return settled

    async def _claim_next(self, conn: psycopg.AsyncConnection, seen: list[int]):
        """Phase A: durably claim the next eligible row (own committed tx)."""
        async with conn.transaction():
            cur = await conn.execute(
                "SELECT id, idempotency_key, channel, payload FROM outbox"
                " WHERE status IN ('PENDING', 'SENDING')"
                "   AND attempts < %s"
                "   AND NOT (id = ANY(%s::bigint[]))"
                " ORDER BY id"
                " FOR UPDATE SKIP LOCKED"
                " LIMIT 1",
                (self._max_attempts, seen),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            outbox_id, key, channel, payload = row

            # Durable dedup claim — committed BEFORE the external call.
            await conn.execute(
                "INSERT INTO side_effect_ledger (idempotency_key, channel, status)"
                " VALUES (%s, %s, 'SENDING')"
                " ON CONFLICT (idempotency_key) DO NOTHING",
                (key, channel),
            )
            led = await conn.execute(
                "SELECT status, provider_id FROM side_effect_ledger"
                " WHERE idempotency_key = %s",
                (key,),
            )
            ledger_status, provider_id = await led.fetchone()

            await conn.execute(
                "UPDATE outbox SET status = 'SENDING', updated_at = now() WHERE id = %s",
                (outbox_id,),
            )
            return outbox_id, key, channel, payload, ledger_status, provider_id

    async def _send_and_settle(
        self, conn, outbox_id, key, channel, payload, ledger_status, provider_id
    ) -> bool:
        """Phase B (send, unless already SENT) + Phase C (settle). Returns True if
        the row reached SENT. Catches per-row failures and re-queues/FAILs them."""
        if ledger_status != "SENT":
            try:
                provider_id = await self._connector.send(key, channel, payload)
            except Exception as exc:  # noqa: BLE001 - isolate one row from the drain
                await self._record_failure(conn, outbox_id, exc)
                return False

        # Phase C: settle (own committed tx). Lock outbox BEFORE ledger to match
        # the claim phase's order (outbox -> ledger), so concurrent dispatchers
        # on the same key can never form a lock cycle. COALESCE keeps a provider
        # id that a prior attempt already recorded.
        async with conn.transaction():
            await conn.execute(
                "UPDATE outbox SET status = 'SENT', updated_at = now() WHERE id = %s",
                (outbox_id,),
            )
            await conn.execute(
                "UPDATE side_effect_ledger"
                " SET status = 'SENT', provider_id = COALESCE(provider_id, %s)"
                " WHERE idempotency_key = %s",
                (provider_id, key),
            )
        return True

    async def _record_failure(self, conn, outbox_id: int, exc: Exception) -> None:
        """Bump attempts + last_error in a SEPARATE committed tx; re-queue under
        the cap, else FAIL. Locks outbox before ledger (consistent ordering).

        On a terminal FAILED, mark the ledger claim FAILED too (it was a SENDING
        row whose effect we are giving up on) so the ledger never strands a
        permanent SENDING/NULL row — a SENT claim is left untouched. The claim is
        otherwise LEFT intact across a retry so a re-drive stays deduped."""
        async with conn.transaction():
            cur = await conn.execute(
                "UPDATE outbox"
                " SET attempts = attempts + 1,"
                "     last_error = %s,"
                "     status = CASE WHEN attempts + 1 >= %s THEN 'FAILED' ELSE 'PENDING' END,"
                "     updated_at = now()"
                " WHERE id = %s"
                " RETURNING status, idempotency_key",
                (str(exc), self._max_attempts, outbox_id),
            )
            new_status, key = await cur.fetchone()
            if new_status == "FAILED":
                await conn.execute(
                    "UPDATE side_effect_ledger SET status = 'FAILED'"
                    " WHERE idempotency_key = %s AND status <> 'SENT'",
                    (key,),
                )
