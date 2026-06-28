-- Exactly-once side-effect boundary (systemdesign §3 + §5.1, HARN-04).
-- Enforced at the database, independent of the orchestration substrate.
-- Idempotent DDL: safe to run on a fresh cluster (initdb) OR against a
-- running cluster (infra/migrate.sh) — re-runs are no-ops.

-- side_effect_ledger: the record that a logical side effect has happened.
-- A second attempt to record the same effect hits UNIQUE(idempotency_key)
-- and is treated as "already done" rather than re-calling the connector.
CREATE TABLE IF NOT EXISTS side_effect_ledger (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idempotency_key text        NOT NULL,
    channel         text        NOT NULL,
    provider_id     text,                 -- id returned by the connector on success
    status          text        NOT NULL DEFAULT 'SENT',
    result          jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT side_effect_ledger_key_uniq UNIQUE (idempotency_key)
);

-- outbox: intent to perform a side effect, written in the SAME transaction
-- that advances run state. A separate at-least-once dispatcher drains it.
CREATE TABLE IF NOT EXISTS outbox (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idempotency_key text        NOT NULL,
    channel         text        NOT NULL,
    payload         jsonb       NOT NULL,
    status          text        NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING', 'SENT', 'FAILED')),
    attempts        integer     NOT NULL DEFAULT 0,
    last_error      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT outbox_key_uniq UNIQUE (idempotency_key)
);

-- Dispatcher claims work with `... WHERE status='PENDING' FOR UPDATE SKIP LOCKED`.
-- A partial index keeps that scan cheap as SENT rows accumulate.
CREATE INDEX IF NOT EXISTS outbox_pending_idx ON outbox (id) WHERE status = 'PENDING';
