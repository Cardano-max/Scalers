-- 11-tenant-documents.sql — the PERSISTENT per-tenant DOCUMENT STORE that every
-- agent (the AG-UI host/supervisor, the LangGraph orchestration nodes, and the
-- realtime voice supervisor) reads + reasons over, RAG-style.
--
-- This is the durable knowledge layer that kills "I don't have access to any
-- uploaded documents": a doc uploaded here SURVIVES sessions/runs (it is tenant-
-- scoped, NOT tied to a chat session id), and deactivating it (active=false) drops
-- it from EVERY agent surface at once.
--
-- Two tables:
--   tenant_documents       — one row per uploaded doc (full content + a compact
--                            summary for the per-turn index). active=false is a
--                            SOFT remove (kept for audit; invisible to every agent).
--   tenant_document_chunks — the doc split into retrievable passages, each carrying
--                            a generated Postgres full-text (tsvector) column +
--                            a GIN index, so retrieval is ts_rank lexical search.
--                            No pgvector / embeddings required — robust + dep-free.
--
-- Idempotent CREATE TABLE IF NOT EXISTS, applied at container init AND best-effort
-- at runtime via studio.documents.ensure_schema (mirrors 09-research-sources.sql /
-- 10-send-audit.sql). The tsvector column is GENERATED ALWAYS (the two-arg
-- to_tsvector(regconfig, text) form is IMMUTABLE), so it is maintained by Postgres
-- and never inserted by hand.

CREATE TABLE IF NOT EXISTS tenant_documents (
    id          TEXT PRIMARY KEY,                 -- doc_<hex>, or a deterministic seed id
    tenant_id   TEXT        NOT NULL,
    name        TEXT        NOT NULL,             -- human title, e.g. "Ladies First Brand & Campaign Playbook"
    kind        TEXT        NOT NULL DEFAULT 'doc',  -- 'doc' | 'brand' | 'strategy' | 'seed' ...
    content     TEXT        NOT NULL,             -- full document text
    summary     TEXT,                             -- compact excerpt for the per-turn index
    active      BOOLEAN     NOT NULL DEFAULT TRUE, -- false = soft-removed (drops from every agent)
    source      TEXT,                             -- 'upload' | 'seed' ... provenance
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tenant_documents_tenant_idx
    ON tenant_documents (tenant_id, active);

CREATE TABLE IF NOT EXISTS tenant_document_chunks (
    id          TEXT PRIMARY KEY,                 -- chk_<hex>
    document_id TEXT        NOT NULL REFERENCES tenant_documents(id) ON DELETE CASCADE,
    tenant_id   TEXT        NOT NULL,
    seq         INTEGER     NOT NULL,             -- ordinal of the chunk within the doc
    heading     TEXT,                             -- nearest section heading (for citations)
    content     TEXT        NOT NULL,             -- the passage text
    tsv         tsvector GENERATED ALWAYS AS (
                    to_tsvector('english', coalesce(heading, '') || ' ' || content)
                ) STORED,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tenant_document_chunks_tsv_idx
    ON tenant_document_chunks USING GIN (tsv);
CREATE INDEX IF NOT EXISTS tenant_document_chunks_doc_idx
    ON tenant_document_chunks (document_id);
CREATE INDEX IF NOT EXISTS tenant_document_chunks_tenant_idx
    ON tenant_document_chunks (tenant_id);
