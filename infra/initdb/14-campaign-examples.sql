-- ju1.2 (SD-MEMORY): the Campaign Example Library — the client's REAL past campaigns
-- (operator-provided screenshots, manually transcribed) stored as tenant-scoped, queryable
-- style/pattern memory. source='operator_screenshot' badges every row as transcribed, not
-- invented; a field not visible in the screenshot stays NULL (never inferred);
-- sent_at/scheduled_for are kept as transcribed TEXT (no timezone inference). This is the
-- initdb twin of studio/campaign_examples_store.py's ensure_schema().
CREATE TABLE IF NOT EXISTS campaign_examples (
    id                 TEXT PRIMARY KEY,
    tenant_id          TEXT NOT NULL,
    campaign_name      TEXT NOT NULL,
    follow_up_to       TEXT,
    status             TEXT,
    sent_at            TEXT,
    scheduled_for      TEXT,
    artist_name        TEXT,
    offer_price_usd    NUMERIC,
    offer_type         TEXT,
    recipient_count    INTEGER,
    delivered_count    INTEGER,
    sent_pending_count INTEGER,
    failed_count       INTEGER,
    dnd_blocked_count  INTEGER,
    message_copy       TEXT,
    message_chars      INTEGER,
    cta                TEXT,
    opt_out_text       TEXT,
    payment_plans      TEXT,
    from_number        TEXT,
    attachment_present BOOLEAN,
    attachment_note    TEXT,
    categories         JSONB,
    artists_selected   JSONB,
    location           TEXT,
    source_screenshot  TEXT,
    source             TEXT NOT NULL DEFAULT 'operator_screenshot',
    provenance         JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, campaign_name)
);
CREATE INDEX IF NOT EXISTS idx_campaign_examples_tenant_artist
    ON campaign_examples (tenant_id, artist_name);

-- Deterministic (keyless-safe) pattern summary extracted from the examples. Each row links
-- the example ids that EVIDENCE it (evidence_example_ids) — a pattern is never asserted
-- without a real example behind it.
CREATE TABLE IF NOT EXISTS campaign_example_patterns (
    id                   TEXT PRIMARY KEY,
    tenant_id            TEXT NOT NULL,
    pattern_key          TEXT NOT NULL,
    description          TEXT NOT NULL,
    evidence_example_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    detail               JSONB,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, pattern_key)
);
