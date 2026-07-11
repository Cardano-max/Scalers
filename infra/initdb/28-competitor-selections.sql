-- 28-competitor-selections.sql — mid-run COMPETITOR-PATTERN selection state (IG
-- competitor-intelligence flow, pause #1).
--
-- When the ig channel plan turns competitor research on, the engine scores the
-- operator-uploaded competitor_posts, surfaces the TOP options and PAUSES
-- ('awaiting') until the operator picks the pattern to MOLD (never copy); the
-- pick ('selected', with the chosen option snapshotted in `choice`) is durable so
-- the resume survives an engine restart, and the run's GET /studio/run/{id}
-- poller reads the pending selection straight from this row.
-- One row per run (run_id PK). Runtime twin: studio/competitor_flow.py ensure_schema().

CREATE TABLE IF NOT EXISTS competitor_selections (
    run_id      TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    session_id  TEXT,
    status      TEXT NOT NULL DEFAULT 'awaiting'
                CHECK (status IN ('awaiting', 'selected')),
    question    TEXT,
    options     JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{postId, handle, caption, url, metrics, totalScore, whyItWorked, visualTags}]
    plan        JSONB,                               -- the plan snapshot the resume re-executes
    choice      JSONB,                               -- the operator's picked option (selected only)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS competitor_selections_tenant_idx
    ON competitor_selections (tenant_id, status);
