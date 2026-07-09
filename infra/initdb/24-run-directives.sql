-- 24-run-directives.sql — the supervisor's full-duplex steering channel.
--
-- The executor consumes PENDING rows at every safe boundary (before each lead):
-- pause/abort stop the fan-out honestly, set_angle/guide_copy redirect subsequent
-- drafts, set_offer switches to another SUBSTANTIATED offer, skip_lead drops a
-- lead with a ledger reason. Applications are recorded as role='supervisor'
-- agent_runs. Directives can only narrow or redirect a run — there is no kind
-- that widens delivery or lifts a gate. Runtime twin: studio/supervisor_control.py.

CREATE TABLE IF NOT EXISTS run_directives (
    id         TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    tenant_id  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    payload    JSONB NOT NULL DEFAULT '{}'::jsonb,
    issued_by  TEXT NOT NULL DEFAULT 'operator',
    status     TEXT NOT NULL DEFAULT 'pending',
    note       TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS run_directives_run_idx ON run_directives (run_id, status);
