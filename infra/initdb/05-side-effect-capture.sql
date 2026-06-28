-- kkg.3 / OBS-03: deep-link + engagement capture on the executed side-effect
-- record, keyed to its idempotency key. Extends the dhv.7 side_effect_ledger
-- (does NOT recreate it). Idempotent: ADD COLUMN IF NOT EXISTS.
--
-- On side-effect success the dispatcher captures the provider result
-- (deep_link/external id/thread_ref); engagement (replies/comments/metrics)
-- captured as it arrives (webhook/poll), merged idempotently. Real URLs land
-- with real tooling (Phase 3/6) behind the SAME schema — mock tooling proves the
-- capture mechanism now.

ALTER TABLE side_effect_ledger
    ADD COLUMN IF NOT EXISTS provider_result jsonb;   -- {deep_link, external_id, thread_ref, ...}; null deep_link disables the link gracefully

ALTER TABLE side_effect_ledger
    ADD COLUMN IF NOT EXISTS deep_link text;          -- extracted external_url for the console (null = no link)

ALTER TABLE side_effect_ledger
    ADD COLUMN IF NOT EXISTS engagement jsonb
        NOT NULL DEFAULT '{"thread": [], "comments": [], "metrics": []}'::jsonb;

ALTER TABLE side_effect_ledger
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
