"""The side-effect boundary — the only way an effect gets queued (HARN-04).

`enqueue` writes the intent into the `outbox` table inside the CALLER's
transaction (the same tx that advances run state), so the intent and the state
change commit together or not at all. A duplicate key is handled gracefully via
`ON CONFLICT DO NOTHING` — it never raises and so never poisons the caller's
transaction; the boundary simply reports `DUPLICATE` and returns the prior
result if one exists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

import psycopg

from engine.sideeffects.keys import Channel


class EnqueueStatus(Enum):
    ENQUEUED = "enqueued"   # this call created the outbox row
    DUPLICATE = "duplicate"  # the key was already enqueued/sent


@dataclass(frozen=True)
class EnqueueResult:
    status: EnqueueStatus
    key: str
    result: dict | None = None  # prior ledger result, if the effect already happened


class SideEffectBoundary:
    """Stateless; operates on whatever connection/transaction the caller owns."""

    async def enqueue(
        self,
        conn: psycopg.AsyncConnection,
        key: str,
        channel: Channel | str,
        payload: dict,
    ) -> EnqueueResult:
        channel_value = channel.value if isinstance(channel, Channel) else str(channel)
        cur = await conn.execute(
            "INSERT INTO outbox (idempotency_key, channel, payload, status)"
            " VALUES (%s, %s, %s, 'PENDING')"
            " ON CONFLICT (idempotency_key) DO NOTHING"
            " RETURNING id",
            (key, channel_value, json.dumps(payload)),
        )
        inserted = await cur.fetchone()
        if inserted is not None:
            return EnqueueResult(EnqueueStatus.ENQUEUED, key)

        # Key already present — fetch any recorded result so callers can return
        # the prior outcome instead of re-doing work.
        prior = await conn.execute(
            "SELECT result FROM side_effect_ledger WHERE idempotency_key = %s",
            (key,),
        )
        row = await prior.fetchone()
        return EnqueueResult(EnqueueStatus.DUPLICATE, key, row[0] if row else None)
