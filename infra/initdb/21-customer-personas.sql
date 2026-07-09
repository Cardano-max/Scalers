-- 21-customer-personas.sql — persona traits + tattoo history (CustomerAcq nmh/ju1).
--
-- Like customers (15-customers.sql), these two tables were provisioned ad-hoc on
-- the original dev machine and their DDL never committed. The studio grounding
-- layer reads both on every personalized run:
--   * studio/customer_research.py:126  SELECT traits, synthetic FROM customer_personas
--   * studio/customer_research.py:224  JOIN customer_personas p ON p.customer_id/tenant_id
--   * studio/customer_research.py:131  SELECT style, artist, date, notes FROM tattoo_history
-- Without them every cohort selection dies with "relation does not exist".
-- Idempotent.

CREATE TABLE IF NOT EXISTS customer_personas (
    tenant_id   TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    -- rich JSONB shape: {"traits": {key: {"value":..., "basis":..., "inferred":...}}}
    traits      JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- TRUE for seeded/demo personas; real research writes FALSE. Honest flag the
    -- grounding layer surfaces so a draft can never silently rest on fixture data.
    synthetic   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, customer_id)
);

CREATE TABLE IF NOT EXISTS tattoo_history (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id   TEXT,
    customer_id TEXT NOT NULL,
    style       TEXT,
    artist      TEXT,
    date        DATE,
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tattoo_history_customer_idx ON tattoo_history (customer_id);
