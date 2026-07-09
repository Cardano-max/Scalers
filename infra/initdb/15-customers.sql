-- 15-customers.sql — tenant customer/lead store (CustomerAcq ju1.1 / nmh.*).
--
-- engine/studio depends on this table everywhere (customer_research.upsert_lead,
-- client_import, campaign_runner cohort selection, dossier), but its DDL was
-- provisioned ad-hoc on the original dev machine and never committed — a fresh
-- clone's initdb jumped 14 -> 16 and every lead write failed with
-- "relation customers does not exist". This file restores the canonical shape
-- from the code's own reads/writes:
--   * INSERT shape: studio/customer_research.py upsert_lead
--   * extended lead columns: customer_research._LEAD_EXT_COLUMNS
--   * import provenance columns: studio/client_import._CUSTOMER_EXT_DDL
-- Idempotent (CREATE ... IF NOT EXISTS) like every other initdb file.

CREATE TABLE IF NOT EXISTS customers (
    id                 TEXT PRIMARY KEY,
    tenant_id          TEXT NOT NULL,
    name               TEXT,
    email              TEXT,
    phone              TEXT,
    linkedin_handle    TEXT,
    ig_handle          TEXT,
    dob                DATE,
    city               TEXT,
    state              TEXT,
    interests          TEXT[] NOT NULL DEFAULT '{}',
    preferred_channels TEXT[] NOT NULL DEFAULT '{}',
    email_opt_in       BOOLEAN NOT NULL DEFAULT FALSE,
    sms_opt_in         BOOLEAN NOT NULL DEFAULT FALSE,
    source             TEXT,

    -- extended tattoo-lead columns (customer_research._LEAD_EXT_COLUMNS adds
    -- these lazily; declared here so a fresh cluster has them from birth)
    notes              TEXT,
    artist             TEXT,
    shop               TEXT,
    lead_stage         TEXT,
    customer_type      TEXT,
    payment_status     TEXT,

    -- client-import provenance (client_import._CUSTOMER_EXT_DDL)
    source_file        TEXT,
    is_test_safe       BOOLEAN,
    consent_status     TEXT,
    data_flags         JSONB,

    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS customers_tenant_idx ON customers (tenant_id);

-- upsert_lead keys on (tenant, lower(email)); rows with no email are allowed
-- (partial index), so the uniqueness contract matches the code's docstring.
CREATE UNIQUE INDEX IF NOT EXISTS customers_tenant_email_uniq
    ON customers (tenant_id, lower(email)) WHERE email IS NOT NULL;
