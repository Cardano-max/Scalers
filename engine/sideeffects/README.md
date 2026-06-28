# engine/sideeffects — exactly-once side-effect boundary

The **only** way a side effect (IG post, Gmail send, DM) happens. Enforced at
Postgres, **independent of the orchestration substrate** — it works the same
whether the spine is the LangGraph Postgres checkpointer or, later, DBOS behind
a thin interface. See `docs/systemdesign.md` §3 + §6.4 (HARN-04).

## The guarantee

A logical side effect fires **exactly once** under retries, concurrency, and
crash-between-write-and-dispatch. Three mechanisms:

1. **Deterministic idempotency key** — `idempotency_key(tenant, channel, target, content)`
   → `tenant:channel:target:contenthash`. Same logical action ⇒ same key (SHA-256, stable across processes).
2. **Postgres `UNIQUE(idempotency_key)`** on both `outbox` and `side_effect_ledger`
   (`infra/initdb/02-side-effect-boundary.sql`).
3. **Transactional outbox** — intent is written in the caller's own DB transaction
   (the one that advances run state); a separate at-least-once **dispatcher** drains it.

## Usage (the §6.5 seam)

```python
from engine.sideeffects import Channel, idempotency_key
from engine.sideeffects.boundary import SideEffectBoundary
from engine.sideeffects.dispatcher import Dispatcher

boundary = SideEffectBoundary()
key = idempotency_key(tenant, Channel.POSTING, target, content)

# In the SAME transaction that advances run state:
async with conn.transaction():
    advance_run_state(conn, ...)
    result = await boundary.enqueue(conn, key, Channel.POSTING, payload)
    # result.status is ENQUEUED on first sight, DUPLICATE on replay (never raises).

# Separately, at least once (a relay/loop): drains PENDING rows, calls the
# connector exactly once per key, records the ledger row.
await Dispatcher(dsn, connector).dispatch_pending()
```

`connector` is any object with `async send(key, channel, payload) -> provider_id`.
In Phase 1 it's a mock; Phase 6 swaps in the real Meta/Gmail MCP behind the same
interface — the boundary does not change.

## Why a duplicate doesn't raise

`enqueue` uses `INSERT ... ON CONFLICT DO NOTHING`. A second attempt with the
same key is reported as `DUPLICATE` instead of throwing — a raised unique
violation would abort the *caller's* transaction (and its state change). Graceful
handling of the constraint is the point.

## Tests

```bash
cd infra && docker compose up -d        # the boundary tests need real Postgres
pip install -e ".[dev]"                  # or use the .venv
pytest engine/sideeffects -v
```

The headline test forces graph-retry + concurrency + a redelivery after a
recorded effect and asserts **one outbox row, one ledger row, one connector
call**.
