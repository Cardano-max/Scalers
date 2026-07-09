-- Durable long-horizon run substrate (P3 / fr1.2 / tlv.1). Until tlv.1 this DDL
-- lived only as inline CREATE ... IF NOT EXISTS in studio/durable_run.py
-- (ensure_schema), created lazily on the first request-path run. The tlv.1
-- startup re-drive supervisor scans durable_run_checkpoint at process start,
-- BEFORE any request-path ensure_schema() runs, so the tables must exist on a
-- fresh cluster from initdb. Kept byte-compatible with durable_run.py's inline
-- DDL (which stays, idempotent, as the belt-and-suspenders path).
--
-- durable_run_checkpoint: one row per durable run — the full-state snapshot +
-- pause marker + cohort cursor. tlv.1 uses state JSONB to freeze the ordered
-- cohort id list + the CampaignPlan + an `executor` tag so a cold-start
-- supervisor can rebuild and re-drive a stranded run with zero external input.
-- durable_step_ledger: the exactly-once replay-skip ledger, UNIQUE(run_id, step_key).
-- Idempotent: safe on a fresh cluster (initdb) and on re-run (infra/migrate.sh).

CREATE TABLE IF NOT EXISTS durable_run_checkpoint (
    run_id      TEXT PRIMARY KEY,
    tenant_id   TEXT        NOT NULL,
    status      TEXT        NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'interrupted', 'completed', 'failed')),
    cursor      INTEGER     NOT NULL DEFAULT 0,          -- monotonic progress marker (cohort ordinal consumed)
    state       JSONB       NOT NULL DEFAULT '{}'::jsonb, -- full run state snapshot (tlv.1: cohort_ids, plan, executor)
    interrupt   JSONB,                                    -- pending operator question; NULL unless paused
    resumes     JSONB       NOT NULL DEFAULT '{}'::jsonb, -- interrupt-ordinal -> operator answer (durable)
    result      JSONB,                                    -- final run result, set at completion
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS durable_run_checkpoint_tenant_idx
    ON durable_run_checkpoint (tenant_id);
-- The supervisor scans for stranded cohort runs by (status, executor tag); a
-- partial index keeps that scan cheap as the checkpoint table grows.
CREATE INDEX IF NOT EXISTS durable_run_checkpoint_running_idx
    ON durable_run_checkpoint (status)
    WHERE status = 'running';

CREATE TABLE IF NOT EXISTS durable_step_ledger (
    id         bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id     TEXT        NOT NULL,
    step_key   TEXT        NOT NULL,
    result     JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT durable_step_ledger_uniq UNIQUE (run_id, step_key)
);
