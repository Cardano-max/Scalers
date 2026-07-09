-- Persistent per-tenant memories (the Studio Host's cross-run memory layer) +
-- the wwy.9 test-hygiene flag. The table itself has so far been created at
-- runtime by ``MemoryStore.ensure_schema`` (engine/memory/store.py) — this file
-- makes it reproducible on a fresh cluster and carries the additive ``is_test``
-- migration for existing ones. Keep the DDL here and in ``ensure_schema`` in
-- sync. Idempotent: safe on a fresh cluster (initdb) and on re-run
-- (infra/migrate.sh).
--
-- WHY is_test (mirrors 16-suppression-consent.sql's contact_memories.is_test,
-- fr1.3): test runs against the shared DB historically wrote ``test_mem_*`` /
-- ``test-stage-*`` artifacts into the LIVE tenant's memories, and recall
-- injected them into real drafting context. Recall now defaults to
-- ``is_test = false``; artifacts are FLAGGED (never deleted) so the history
-- stays auditable.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memories (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    subject_type  TEXT NOT NULL
        CHECK (subject_type IN ('customer','campaign','conversation','fact')),
    subject_id    TEXT,
    text          TEXT NOT NULL,
    embedding     vector(384),
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    content_hash  TEXT NOT NULL,
    is_test       BOOLEAN NOT NULL DEFAULT false,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Idempotent additive migration for EXISTING clusters (the CREATE above is a
-- no-op there, so the new column must also be added explicitly).
ALTER TABLE memories ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS memories_tenant_idx
    ON memories (tenant_id);
CREATE INDEX IF NOT EXISTS memories_subject_idx
    ON memories (tenant_id, subject_type, subject_id);
CREATE UNIQUE INDEX IF NOT EXISTS memories_natural_key
    ON memories (tenant_id, subject_type, COALESCE(subject_id, ''), content_hash);
