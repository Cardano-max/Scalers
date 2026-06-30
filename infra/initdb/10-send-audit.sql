-- 10-send-audit.sql — the operator-action audit trail for campaign-level sending.
--
-- One row per operator-initiated send decision at the campaign level: a
-- "send eligible" batch run, or an OVERRIDE of a draft that did NOT pass the
-- confidence/compliance bar. The override path is the sensitive one — it is the
-- ONLY way a below-bar / flagged draft reaches the real send path, and it MUST
-- leave a durable record of who did it and why.
--
-- This table is additive and never touches actions/* or autonomy/*. The actual
-- send still goes through actions.publish.approve_and_publish (atomic exactly-once
-- claim + gmail allow-list/redirect); this is the audit, not the send.
--
-- Idempotent CREATE TABLE IF NOT EXISTS, applied at container init AND at runtime
-- via actions.audit.ensure_schema (mirrors 08-actions.sql / 09-research-sources.sql).

CREATE TABLE IF NOT EXISTS send_audit (
    id          TEXT PRIMARY KEY,                 -- aud_<hex>
    action_id   TEXT        NOT NULL,             -- the action the operator acted on
    run_id      TEXT,                             -- campaign run it belongs to
    tenant_id   TEXT,
    kind        TEXT        NOT NULL,             -- 'send_eligible' | 'override'
    operator    TEXT,                             -- who initiated it (best-effort identity)
    reason      TEXT,                             -- REQUIRED on override; the justification
    eligible    BOOLEAN,                          -- was the draft eligible at decision time
    conf        DOUBLE PRECISION,                 -- confidence snapshot at decision time
    threshold   DOUBLE PRECISION,
    esc_kind    TEXT,                             -- escalation kind snapshot
    result      TEXT,                             -- the action status after the send attempt
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS send_audit_action_idx ON send_audit (action_id);
CREATE INDEX IF NOT EXISTS send_audit_run_idx    ON send_audit (run_id);
CREATE INDEX IF NOT EXISTS send_audit_kind_idx   ON send_audit (kind);
