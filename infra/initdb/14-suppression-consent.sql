-- 14-suppression-consent.sql — cross-channel STOP/suppression ledger + consent
-- + per-recipient send events + bi-temporal contact memories (CustomerAcq-t90.3,
-- blueprint §2-B3 / §4.2 STOP-DND / §4.3-2/-3/-5).
--
-- Numbered 14 (not 08) because the phase3 integration branch owns 08-13.
-- Idempotent DDL: safe on a fresh cluster (initdb) or a running one (migrate).
-- Depends on 02-side-effect-boundary.sql (ALTERs the outbox).

-- suppression_ledger: ONE cross-channel source of opt-out truth. Every channel
-- reads it (sms gate, send-time eligibility, audience creation, email path).
-- channel='all' suppresses everywhere (FCC any-reasonable-means revocation);
-- reason records HOW it arrived (stop | email_unsub | web_form | verbal |
-- manual | carrier_30003..30006). Suppression is permanent — rows are never
-- deleted. The natural-key UNIQUE makes mirror ingestion (Twilio webhooks,
-- unsub feeds) idempotent.
CREATE TABLE IF NOT EXISTS suppression_ledger (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id     text        NOT NULL,
    identifier    text        NOT NULL,          -- E.164 phone or normalized email
    channel       text        NOT NULL DEFAULT 'all',  -- sms|email|instagram|facebook|all
    reason        text        NOT NULL,
    raw_utterance text,                          -- the verbatim STOP text / unsub note
    occurred_at   timestamptz NOT NULL,          -- when the human opted out (valid time)
    recorded_at   timestamptz NOT NULL DEFAULT now(),  -- when we learned it (txn time)
    CONSTRAINT suppression_event_uniq UNIQUE (tenant_id, identifier, channel, reason, occurred_at)
);
CREATE INDEX IF NOT EXISTS suppression_lookup_idx
    ON suppression_ledger (tenant_id, identifier);

-- consent: PEWC rows with provenance (blueprint 4.3-2). A STOP writes
-- revoked_at here IN THE SAME TRANSACTION as the suppression row, so the sms
-- gate's consent check and suppression check flip together (defense-in-depth).
CREATE TABLE IF NOT EXISTS consent (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id     text        NOT NULL,
    identifier    text        NOT NULL,
    channel       text        NOT NULL,          -- sms | email
    source        text        NOT NULL,          -- web_form | pos | import | ...
    granted_at    timestamptz NOT NULL,
    revoked_at    timestamptz,                   -- NULL = active
    revoke_source text,                          -- how the revocation arrived
    recorded_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT consent_grant_uniq UNIQUE (tenant_id, identifier, channel, granted_at)
);
CREATE INDEX IF NOT EXISTS consent_lookup_idx
    ON consent (tenant_id, identifier, channel);

-- send_events: one row per DELIVERY attempt that reached a provider (or the
-- TEST-MODE redirect) — the frequency-cap window reads this. Sandbox sends
-- write here too (mode='test_redirect'): the machinery is proven before
-- go-live and a redirected send still consumes the recipient's window.
-- idempotency_key ties the row to the outbox/side_effect_ledger entry; UNIQUE
-- makes the settle-transaction mirror write idempotent under retry.
CREATE TABLE IF NOT EXISTS send_events (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id       text        NOT NULL,
    identifier      text        NOT NULL,
    channel         text        NOT NULL,
    kind            text        NOT NULL DEFAULT 'promo',  -- promo | transactional
    mode            text        NOT NULL,        -- live | test_redirect
    idempotency_key text,
    occurred_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT send_events_key_uniq UNIQUE (idempotency_key)
);
CREATE INDEX IF NOT EXISTS send_events_recipient_idx
    ON send_events (tenant_id, identifier, channel, occurred_at DESC);

-- contact_memories: bi-temporal contact-preference memories (blueprint 4.3-5).
-- valid_from/valid_to is VALID time (when the preference held), recorded_at is
-- TRANSACTION time (when we learned it). A STOP SUPERSEDES the open rows
-- (valid_to + superseded_by -> the new do-not-contact row) — never deletes, so
-- history stays auditable.
CREATE TABLE IF NOT EXISTS contact_memories (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id     text        NOT NULL,
    identifier    text        NOT NULL,
    content       jsonb       NOT NULL,
    valid_from    timestamptz NOT NULL,
    valid_to      timestamptz,                   -- NULL = current
    superseded_by bigint REFERENCES contact_memories(id),
    recorded_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS contact_memories_open_idx
    ON contact_memories (tenant_id, identifier) WHERE valid_to IS NULL;

-- delivery_events: provider status callbacks (queued/sent/delivered/
-- undelivered/failed) per message (§4.3-10). UNIQUE (provider_sid, status)
-- makes webhook retries no-ops. Written under the sandbox redirect too — the
-- machinery is proven before go-live.
CREATE TABLE IF NOT EXISTS delivery_events (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id    text        NOT NULL,
    identifier   text        NOT NULL,
    channel      text        NOT NULL DEFAULT 'sms',
    provider_sid text,
    status       text        NOT NULL,
    error_code   integer,
    raw          jsonb,
    occurred_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS delivery_events_sid_status_uniq
    ON delivery_events (provider_sid, status);
CREATE INDEX IF NOT EXISTS delivery_events_recipient_idx
    ON delivery_events (tenant_id, identifier, occurred_at DESC);

-- carrier_errors: every carrier delivery error, for the 30007 spike alert
-- (30003-30006 ALSO auto-suppress via a suppression_ledger row). provider_sid
-- (the provider's message sid) is UNIQUE so webhook retries cannot inflate the
-- spike count (NULLs stay distinct for sources with no sid).
CREATE TABLE IF NOT EXISTS carrier_errors (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id    text        NOT NULL,
    identifier   text        NOT NULL,
    code         integer     NOT NULL,
    provider_sid text,
    occurred_at  timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE carrier_errors ADD COLUMN IF NOT EXISTS provider_sid text;
CREATE UNIQUE INDEX IF NOT EXISTS carrier_errors_sid_uniq
    ON carrier_errors (provider_sid);
CREATE INDEX IF NOT EXISTS carrier_errors_tenant_code_idx
    ON carrier_errors (tenant_id, code, occurred_at DESC);

-- Per-RECIPIENT exactly-once at staging (t90.3 layer a). The outbox gains the
-- recipient/content identity columns; the partial UNIQUE enforces "one
-- unsettled draft per (tenant, target, content)" AT THE DATABASE, independent
-- of how the idempotency key was derived (this catches key-scheme drift — the
-- run-scoped-key bug that produced 3 byte-identical pending drafts). Columns
-- are nullable: pre-existing rows and non-recipient channels are exempt
-- (Postgres treats NULLs as distinct in unique indexes).
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS tenant_id text;
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS target    text;
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS draft_md5 text;
CREATE UNIQUE INDEX IF NOT EXISTS outbox_recipient_draft_pending_uniq
    ON outbox (tenant_id, target, draft_md5)
    WHERE status IN ('PENDING', 'SENDING');
CREATE INDEX IF NOT EXISTS outbox_tenant_target_unsettled_idx
    ON outbox (tenant_id, target)
    WHERE status IN ('PENDING', 'SENDING');

-- The DATABASE (not a calling convention) refuses an sms outbox row that
-- lacks its recipient identity: without this, the generic boundary enqueue
-- (NULL tenant_id/target/draft_md5) would slip past the partial unique index
-- (NULLs are distinct) and the run-scoped-key duplicate bug could recur.
ALTER TABLE outbox DROP CONSTRAINT IF EXISTS outbox_sms_identity_check;
ALTER TABLE outbox ADD CONSTRAINT outbox_sms_identity_check
    CHECK (channel <> 'sms'
           OR (tenant_id IS NOT NULL AND target IS NOT NULL AND draft_md5 IS NOT NULL));
