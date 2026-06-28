# engine/sideeffects — exactly-once side-effect boundary

The **only** way a side effect (IG post, Gmail send, DM) happens. Enforced at
Postgres, **independent of the orchestration substrate** — it works the same
whether the spine is the LangGraph Postgres checkpointer or, later, DBOS behind
a thin interface. See `docs/systemdesign.md` §3 + §6.4 (HARN-04).

## The guarantee (and its one precondition)

A logical side effect produces **exactly one external effect** under retries,
concurrency, and crash-between-send-and-commit — **provided the connector is
idempotent on the key**. Exactly-once for a non-transactional external effect is
impossible without an idempotent consumer (the two-generals problem); real
IG/Gmail APIs supply an idempotency token, so we require and document that
contract (see `dispatcher.Connector`).

Four mechanisms:

1. **Deterministic idempotency key** — `idempotency_key(tenant, channel, target, content)`
   → `tenant:channel:target:contenthash`. Same logical action ⇒ same key (SHA-256, stable across processes).
2. **Postgres `UNIQUE(idempotency_key)`** on both `outbox` and `side_effect_ledger`
   (`infra/initdb/02-side-effect-boundary.sql`).
3. **Transactional outbox** — intent is written in the caller's own DB transaction
   (the one that advances run state); a separate at-least-once **dispatcher** drains it.
4. **Durable `SENDING` claim before the call** — the dispatcher commits a ledger
   `SENDING` row in its OWN transaction *before* invoking the connector, in three
   separate committed steps (claim → send → settle). A crash after the effect can
   never roll the claim back and re-acquire ownership; recovery re-drives through
   the idempotent connector, which dedupes. (The earlier single-tx design rolled
   the claim back on a crash-after-send and double-fired — fixed here.)

## Usage (the §6.5 seam)

```python
from sideeffects import Channel, idempotency_key
from sideeffects.boundary import SideEffectBoundary
from sideeffects.dispatcher import Dispatcher

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

`connector` is any object satisfying the `dispatcher.Connector` protocol —
`async send(key, channel, payload) -> provider_id`, **idempotent on `key`**. In
Phase 1 it's a keyed mock; Phase 6 swaps in the real Meta/Gmail MCP (with the
provider idempotency token) behind the same interface — the boundary does not
change.

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

The headline crash-injection test fires the effect, kills the dispatcher before
the settle commits, then runs a fresh dispatcher and asserts **exactly one
external effect** (`connector.call_count == 1`) even though the connector is
invoked twice and the provider dedupes the second. Other tests cover graph-retry
idempotency, concurrent enqueue/dispatch, the already-SENT skip, recovery of a
hard-crash `SENDING` claim, and poison-pill isolation (one bad row never aborts
the drain). All run against the real local Postgres.
