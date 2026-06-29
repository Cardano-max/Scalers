-- 08-actions.sql — the review-queue read model (a9m.7 / OBS console).
--
-- One row per proposed side-effecting action (an outreach email, a post, a
-- comment reply). The engine's decision path writes a PENDING row when an action
-- routes to REVIEW (autonomy HOLD); the console renders these as the review
-- queue, the operator approves, and approve->publish flips status to sent.
--
-- The jury card (per-judge voice/safety/appr), pooled confidence, gates, and
-- agreement are NOT duplicated here — they live on the linked autonomy_decisions
-- + autonomy_jury rows (joined by decision_id). This table holds the action's
-- own content + lifecycle. Maps 1:1 to the console's Action/Approval/Activity
-- shapes in web/lib/data/models.ts.

CREATE TABLE IF NOT EXISTS actions (
    id              TEXT PRIMARY KEY,                 -- act_<hex>
    tenant_id       TEXT NOT NULL,
    decision_id     TEXT,                             -- -> autonomy_decisions(decision_id): jury/confidence/gates
    run_id          TEXT,
    type            TEXT NOT NULL,                    -- outreach | comment | dm | post
    channel         TEXT NOT NULL,                    -- gmail | instagram | facebook
    worker          TEXT,                             -- Outreach | Publisher | Responder
    target          TEXT,                             -- recipient email / @handle / comment id
    subject         TEXT,                             -- email subject (nullable)
    context         TEXT,                             -- "replying to" context (nullable)
    draft           TEXT NOT NULL,                    -- the body / caption / reply text
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|sending|sent|rejected|failed
    autonomy        TEXT,                             -- auto | approved  (for Activity view)
    conf            DOUBLE PRECISION,                 -- pooled confidence snapshot (mirrors decision)
    threshold       DOUBLE PRECISION,
    esc_kind        TEXT,                             -- confidence | safety | gate | split | media
    esc_label       TEXT,
    idempotency_key TEXT UNIQUE,                      -- exactly-once on publish
    deep_link       TEXT,                             -- result URL after a real send
    outcome_label   TEXT,                             -- Sent | Published | Replied
    outcome_kind    TEXT,                             -- success | teal | neutral
    recommend       TEXT,                             -- optional jury recommendation note
    thinking        JSONB NOT NULL DEFAULT '[]'::jsonb,   -- agent reasoning trace (Activity)
    engagement      JSONB NOT NULL DEFAULT '[]'::jsonb,   -- [{label,value}] post-send engagement
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at     TIMESTAMPTZ,
    sent_at         TIMESTAMPTZ,
    CONSTRAINT actions_status_check CHECK (
        status IN ('pending','approved','sending','sent','rejected','failed'))
);

CREATE INDEX IF NOT EXISTS actions_tenant_status_idx ON actions (tenant_id, status);
CREATE INDEX IF NOT EXISTS actions_decision_idx      ON actions (decision_id);
CREATE INDEX IF NOT EXISTS actions_created_idx       ON actions (created_at);
