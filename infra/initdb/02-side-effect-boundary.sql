-- Exactly-once side-effect boundary (systemdesign §3 + §5.1, HARN-04).
-- Enforced at the database, independent of the orchestration substrate.
-- Idempotent DDL: safe to run on a fresh cluster (initdb) OR against a
-- running cluster (infra/migrate.sh) — re-runs are no-ops.

-- side_effect_ledger: the durable record of a logical side effect. The
-- dispatcher inserts a 'SENDING' claim BEFORE calling the connector and flips
-- it to 'SENT' (with provider_id) after. A second attempt to claim the same key
-- hits UNIQUE(idempotency_key) and is treated as "already in flight / done".
CREATE TABLE IF NOT EXISTS side_effect_ledger (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idempotency_key text        NOT NULL,
    channel         text        NOT NULL,
    provider_id     text,                 -- id returned by the connector on success
    status          text        NOT NULL DEFAULT 'SENDING',  -- SENDING -> SENT
    result          jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT side_effect_ledger_key_uniq UNIQUE (idempotency_key)
);

-- outbox: intent to perform a side effect, written in the SAME transaction that
-- advances run state. A separate at-least-once dispatcher drains it. Lifecycle:
-- PENDING -> SENDING (claimed, in flight) -> SENT, or -> FAILED past max attempts.
CREATE TABLE IF NOT EXISTS outbox (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idempotency_key text        NOT NULL,
    channel         text        NOT NULL,
    payload         jsonb       NOT NULL,
    status          text        NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING', 'SENDING', 'SENT', 'FAILED')),
    attempts        integer     NOT NULL DEFAULT 0,
    last_error      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT outbox_key_uniq UNIQUE (idempotency_key)
);

-- Upgrade an existing cluster's CHECK to allow the 'SENDING' claim state
-- (CREATE TABLE IF NOT EXISTS won't change an already-created constraint).
ALTER TABLE outbox DROP CONSTRAINT IF EXISTS outbox_status_check;
ALTER TABLE outbox ADD CONSTRAINT outbox_status_check
    CHECK (status IN ('PENDING', 'SENDING', 'SENT', 'FAILED'));

-- The dispatcher claims work with
--   ... WHERE status IN ('PENDING','SENDING') ... FOR UPDATE SKIP LOCKED.
-- A partial index keeps that scan cheap as SENT rows accumulate.
DROP INDEX IF EXISTS outbox_pending_idx;
CREATE INDEX IF NOT EXISTS outbox_unsettled_idx
    ON outbox (id) WHERE status IN ('PENDING', 'SENDING');
