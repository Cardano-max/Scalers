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

from harness.hold import DEFAULT_HOLD_REGISTRY, HoldRegistry
from sideeffects.keys import Channel


class EnqueueStatus(Enum):
    ENQUEUED = "enqueued"   # this call created the outbox row
    DUPLICATE = "duplicate"  # the key was already enqueued/sent
    HELD = "held"           # refused by the bead-439 hold gate (CustomerAcq-4z2)


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
        *,
        tenant_id: str,
        hold_registry: HoldRegistry = DEFAULT_HOLD_REGISTRY,
    ) -> EnqueueResult:
        channel_value = channel.value if isinstance(channel, Channel) else str(channel)
        # bead-439 two-layer HOLD (CustomerAcq-4z2): an INDEPENDENT gate at the
        # send boundary. A held (tenant, channel) cannot enqueue no matter how the
        # routing decision was reached — defense-in-depth behind the router's HOLD.
        # The default registry holds everything (fail-safe), so nothing publishes
        # until an operator explicitly lifts a tenant/channel.
        if hold_registry.is_held(tenant_id, channel_value):
            return EnqueueResult(EnqueueStatus.HELD, key)
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
