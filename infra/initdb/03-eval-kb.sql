-- Phase-2 eval-spine KB scaffolding (KNOW-01 / rvy.2), per docs/adr/phase-2-eval-spine.md
-- Decisions 1-2. Extends the Phase-1 Postgres+pgvector stack (does NOT recreate
-- it). Idempotent: safe on a fresh cluster (initdb) and re-runs.
--
-- Three tenant-scoped tables — the eval store, disjoint from the runtime status
-- store (runs/actions/outbox): the item under test, its per-rater labels, and
-- the metric history that is the gating source of truth.

CREATE EXTENSION IF NOT EXISTS vector;

-- gold_example: one row per example under test. TEXT + CHECK instead of native
-- enums so the migration stays idempotent (no CREATE TYPE re-run hazard).
CREATE TABLE IF NOT EXISTS gold_example (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       text        NOT NULL,
    engine          text        NOT NULL
                    CHECK (engine IN ('POSTING', 'OUTREACH', 'ENGAGEMENT', 'RESEARCH')),
    cell            text        NOT NULL,
    input           jsonb       NOT NULL,
    expected        jsonb,
    rubric_dimensions text[]    NOT NULL DEFAULT '{}',
    split           text        NOT NULL DEFAULT 'CALIBRATION'
                    CHECK (split IN ('CALIBRATION', 'HOLDOUT', 'SMOKE')),
    label_version   integer     NOT NULL DEFAULT 1,
    -- sha256 of the canonical input; the natural-key component that makes
    -- re-ingest idempotent without hashing inside SQL.
    content_hash    text        NOT NULL,
    embedding       vector(384),
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text,
    -- Re-ingesting the same logical example (same tenant/engine/cell/content at a
    -- label version) is a no-op rather than a duplicate row.
    CONSTRAINT gold_example_natural_key
        UNIQUE (tenant_id, engine, cell, content_hash, label_version)
);

-- gold_label: per-rater × per-dimension labels (never collapsed) so kappa /
-- %-agreement is computable and a relabel adds rows instead of overwriting.
CREATE TABLE IF NOT EXISTS gold_label (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    example_id      uuid        NOT NULL REFERENCES gold_example(id) ON DELETE CASCADE,
    tenant_id       text        NOT NULL,   -- denormalized for tenant-isolated reads
    rater_id        text        NOT NULL,
    dimension       text        NOT NULL,
    label           jsonb       NOT NULL,
    label_version   integer     NOT NULL DEFAULT 1,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT gold_label_natural_key
        UNIQUE (example_id, rater_id, dimension, label_version)
);

-- eval_metric: append-only metric history + the gating source of truth. A
-- label/model/prompt bump produces a NEW identity row; old rows stay as history.
CREATE TABLE IF NOT EXISTS eval_metric (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope           text        NOT NULL DEFAULT 'TENANT'
                    CHECK (scope IN ('TENANT', 'GLOBAL')),
    tenant_id       text,       -- NOT NULL when scope=TENANT (CHECK below)
    engine          text,
    cell            text,
    metric          text        NOT NULL,
    value           double precision NOT NULL,
    threshold       double precision,
    direction       text        CHECK (direction IN ('GTE', 'LTE')),
    passed          boolean,
    run_kind        text        CHECK (run_kind IN ('PER_COMMIT', 'PER_PROMOTION')),
    label_version   integer,
    model_pins_hash text,
    prompt_version  text,
    dataset_hash    text,
    git_sha         text,
    langfuse_trace_id text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT eval_metric_tenant_scope
        CHECK (scope = 'GLOBAL' OR tenant_id IS NOT NULL)
);

-- Indexes: every read filters tenant_id; the vector column is ANN-searchable so
-- examples stay first-class KB citizens (KNOW-02 grounding reuses them).
CREATE INDEX IF NOT EXISTS gold_example_tenant_idx
    ON gold_example (tenant_id, engine, cell, label_version);
CREATE INDEX IF NOT EXISTS gold_label_tenant_idx ON gold_label (tenant_id, example_id);
CREATE INDEX IF NOT EXISTS eval_metric_tenant_idx
    ON eval_metric (tenant_id, engine, cell, metric, created_at);
CREATE INDEX IF NOT EXISTS gold_example_embedding_idx
    ON gold_example USING hnsw (embedding vector_cosine_ops);

-- ── Row-Level Security (defense-in-depth tenant isolation) ────────────────────
-- The data-access layer always filters tenant_id; RLS enforces the same at the
-- DB for any non-superuser connection. The production app connects as the
-- NOSUPERUSER `scalers_app` role with `app.current_tenant` set per request.
-- (Superusers bypass RLS by design — the DAL filter is the always-on guarantee.)
-- Best-effort: a Postgres whose connecting user lacks CREATEROLE (some CI
-- services) still gets the tables + RLS policies; only the convenience role is
-- skipped. RLS applies to ANY non-superuser role, not just scalers_app.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'scalers_app') THEN
        CREATE ROLE scalers_app LOGIN PASSWORD 'scalers_app';
    END IF;
    GRANT SELECT, INSERT, UPDATE, DELETE ON gold_example, gold_label, eval_metric TO scalers_app;
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'skipping scalers_app role/grants (insufficient privilege); RLS still enforced';
END $$;

ALTER TABLE gold_example ENABLE ROW LEVEL SECURITY;
ALTER TABLE gold_example FORCE ROW LEVEL SECURITY;
ALTER TABLE gold_label  ENABLE ROW LEVEL SECURITY;
ALTER TABLE gold_label  FORCE ROW LEVEL SECURITY;
ALTER TABLE eval_metric ENABLE ROW LEVEL SECURITY;
ALTER TABLE eval_metric FORCE ROW LEVEL SECURITY;

-- Tenant-scoped tables: a row is visible/writable only for the session's tenant.
DROP POLICY IF EXISTS gold_example_tenant_isolation ON gold_example;
CREATE POLICY gold_example_tenant_isolation ON gold_example
    USING (tenant_id = current_setting('app.current_tenant', true))
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true));

DROP POLICY IF EXISTS gold_label_tenant_isolation ON gold_label;
CREATE POLICY gold_label_tenant_isolation ON gold_label
    USING (tenant_id = current_setting('app.current_tenant', true))
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true));

-- eval_metric: tenant rows are isolated; GLOBAL rows (tenant_id IS NULL) are
-- readable/writable by any tenant session.
DROP POLICY IF EXISTS eval_metric_tenant_isolation ON eval_metric;
CREATE POLICY eval_metric_tenant_isolation ON eval_metric
    USING (tenant_id IS NULL OR tenant_id = current_setting('app.current_tenant', true))
    WITH CHECK (tenant_id IS NULL OR tenant_id = current_setting('app.current_tenant', true));
