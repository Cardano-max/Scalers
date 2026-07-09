-- 23-artwork-selections.sql — mid-run artwork selection state (engine-core item 3).
--
-- When a campaign run wants to attach artwork, the engine surfaces the TOP matching
-- portfolio pieces and PAUSES ('awaiting') until the operator picks one; the pick
-- ('selected') is durable so the resume survives an engine restart, and the run's
-- GET /studio/run/{id} poller reads the pending selection straight from this row.
-- One row per run (run_id PK). Runtime twin: studio/artwork_flow.py ensure_schema().

CREATE TABLE IF NOT EXISTS artwork_selections (
    run_id      TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    session_id  TEXT,
    status      TEXT NOT NULL DEFAULT 'awaiting'
                CHECK (status IN ('awaiting', 'selected')),
    question    TEXT,
    options     JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{assetId, artifactId, styles, motifs, why}]
    plan        JSONB,                               -- the plan snapshot the resume re-executes
    asset_id    TEXT,                                -- the operator's pick (selected only)
    artifact_id TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS artwork_selections_tenant_idx
    ON artwork_selections (tenant_id, status);
