-- 18-scheduled-job-runs.sql — exactly-once-per-day CLAIM-THEN-TRANSITION ledger
-- for the proactive daily scanner (CustomerAcq-fr1.1 AC-1 + AC-9).
--
-- One row per (tenant_id, job_id, fire_date). The worker CLAIMS a fire_date with
-- INSERT ... ON CONFLICT DO NOTHING (the UNIQUE below is the exactly-once guard —
-- the same claim idiom the actions store already trusts, infra/initdb/08-actions.sql
-- / engine/actions/store.py), then TRANSITIONS the claimed row to completed/failed.
-- A crash after claim leaves a 'claimed' row a restart can DETECT (status stays
-- 'claimed') and re-drive or surface — so a crash never SILENTLY consumes the
-- fire_date (claim-only would fail safe for sends but fail silent for liveness).
CREATE TABLE IF NOT EXISTS scheduled_job_runs (
    id           TEXT        PRIMARY KEY,
    tenant_id    TEXT        NOT NULL,
    job_id       TEXT        NOT NULL,
    fire_date    DATE        NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'claimed'
                 CHECK (status IN ('claimed', 'completed', 'failed')),
    claimed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    detail       JSONB,
    UNIQUE (tenant_id, job_id, fire_date)
);

-- The cheap index a restart rides to surface crash-mid-scan runs (still 'claimed').
CREATE INDEX IF NOT EXISTS scheduled_job_runs_claimed_idx
    ON scheduled_job_runs (claimed_at)
    WHERE status = 'claimed';
