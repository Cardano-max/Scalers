-- Phase-3 content/voice KB — the `kb_chunks` partition (KNOW-02 / a9m.3, Stream B0
-- = bead cym). Per docs/adr/phase-3-content-engine-slice.md Decision 3 +
-- systemdesign §5.1. Extends the Phase-1 pgvector stack and the rvy.2 eval KB
-- (03-eval-kb.sql) and the 1mk.9 grounding partition (04-grounding-kb.sql) — does
-- NOT recreate any of them. Idempotent: safe on a fresh cluster (initdb) and on
-- re-run (infra/migrate.sh).
--
-- WHY a separate table (not gold_example, not practitioner_wisdom):
--   * gold_example is the eval store — "items under test", per-rater labels.
--   * practitioner_wisdom is GLOBAL brand-agnostic few-shot grounding (no tenant).
--   * kb_chunks is the TENANT-scoped past-content partition: the client's own past
--     posts / voice samples, retrieved by similarity to ground the Copywriter cell
--     in *this* tenant's voice (KNOW-02). Tenant isolation is therefore mandatory
--     here (RLS + DAL filter), exactly like gold_example.
--
-- The `kind` partition mirrors the ADR: `post` (a past published post) and `voice`
-- (a curated voice sample). The embed dim matches gold_example.embedding (vector(384)).

CREATE EXTENSION IF NOT EXISTS vector;

-- kb_chunks: one row per tenant-owned content/voice chunk. TEXT + CHECK (no native
-- enum) so the migration stays idempotent (no CREATE TYPE re-run hazard).
CREATE TABLE IF NOT EXISTS kb_chunks (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       text        NOT NULL,
    kind            text        NOT NULL
                    CHECK (kind IN ('post', 'voice')),
    content         text        NOT NULL,
    -- e.g. {"on_voice": true, "engagement": 0.12, "posted_at": "..."} — opaque to
    -- the schema; the retrieval surfaces it on Exemplar.metrics for the cell/console.
    metrics         jsonb       NOT NULL DEFAULT '{}',
    -- Holdout-disjoint invariant (voice-grounding-contract §1, qa1/KNOW-02): a chunk
    -- tagged holdout is content the rvy.4 brand-voice holdout later SCORES against —
    -- so it must NEVER be handed back as grounding (the engine would be grounding on
    -- the very content it is graded on). `voice_exemplars` filters `is_holdout` out.
    is_holdout      boolean     NOT NULL DEFAULT false,
    -- sha256 of `content`; the natural-key component that makes re-ingest idempotent
    -- (a re-load of the same chunk never dups) without hashing inside SQL.
    content_hash    text        NOT NULL,
    embedding       vector(384),
    created_at      timestamptz NOT NULL DEFAULT now(),
    -- Re-ingesting the same logical chunk (same tenant/kind/content) is a no-op
    -- rather than a duplicate row; distinct content (different hash) is all kept.
    CONSTRAINT kb_chunks_natural_key UNIQUE (tenant_id, kind, content_hash)
);

-- Retrieval filters tenant_id (+ kind); the vector column is ANN-searchable so
-- voice grounding fetches the nearest exemplars by cosine.
CREATE INDEX IF NOT EXISTS kb_chunks_tenant_idx ON kb_chunks (tenant_id, kind);
CREATE INDEX IF NOT EXISTS kb_chunks_embedding_idx
    ON kb_chunks USING hnsw (embedding vector_cosine_ops);

-- ── Row-Level Security (defense-in-depth tenant isolation) ────────────────────
-- The DAL always filters tenant_id; RLS enforces the same at the DB for any
-- non-superuser connection (scalers_app, NOSUPERUSER, with app.current_tenant set
-- per request). Superusers bypass RLS by design — the DAL filter is the always-on
-- guarantee. Mirrors 03-eval-kb.sql. Best-effort role grant (the role exists once
-- 03-eval-kb.sql ran; skip cleanly if privileges are thin).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'scalers_app') THEN
        CREATE ROLE scalers_app LOGIN PASSWORD 'scalers_app';
    END IF;
    GRANT SELECT, INSERT, UPDATE, DELETE ON kb_chunks TO scalers_app;
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'skipping scalers_app grant on kb_chunks (insufficient privilege); RLS still enforced';
END $$;

ALTER TABLE kb_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE kb_chunks FORCE ROW LEVEL SECURITY;

-- A row is visible/writable only for the session's tenant.
DROP POLICY IF EXISTS kb_chunks_tenant_isolation ON kb_chunks;
CREATE POLICY kb_chunks_tenant_isolation ON kb_chunks
    USING (tenant_id = current_setting('app.current_tenant', true))
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true));
