-- 20-context-artifacts.sql — the UNIVERSAL context-artifact registry (nmh.4).
--
-- One row per uploaded FILE, whatever its kind: a customer CSV, a brand-voice
-- doc, a PDF/DOCX, an image, an artwork asset, a campaign screenshot. Today
-- these live in four disjoint silos (tenant_documents for text docs, the
-- customers table for CSV rows, the plan blob for brand notes, the assets table
-- for artwork JSONB) and NOTHING lists them together — so the voice supervisor
-- cannot answer "can you see the CSV / how many images". This table is the
-- single source of truth for "what files exist" that BOTH the voice supervisor
-- and every campaign agent read.
--
-- It does NOT replace the silos it unifies: the CSV rows still live in
-- `customers`, the doc chunks still live in `tenant_document_chunks` (RAG). An
-- artifact row is the unified INDEX + a pointer (meta->>'document_id', etc.) +
-- the parsed content/preview needed to answer state questions and ground agents.
--
-- Mirrors the tenant_documents conventions: tenant-scoped, soft-remove via
-- `active`, TEXT ids, idempotent CREATE ... IF NOT EXISTS (applied at container
-- init AND best-effort at runtime via studio.artifacts.ensure_schema).

CREATE TABLE IF NOT EXISTS context_artifacts (
    id            TEXT PRIMARY KEY,                 -- art_<hex>, or a deterministic id
    tenant_id     TEXT        NOT NULL,
    name          TEXT        NOT NULL,             -- original filename / human title
    artifact_type TEXT        NOT NULL              -- the coarse kind the supervisor counts by
                  CHECK (artifact_type IN (
                      'csv', 'brand_voice', 'document', 'pdf',
                      'image', 'artwork', 'screenshot', 'other')),
    media_type    TEXT,                             -- MIME, e.g. 'text/csv' | 'image/png' | 'application/pdf'
    summary       TEXT,                             -- honest one-liner: "CSV: 500 rows; columns: name, email"
    parsed_content TEXT,                            -- extracted text (docs/CSV); NULL/empty for a not-yet-parsed image
    preview       TEXT,                             -- bounded data-uri thumbnail for an image; NULL otherwise
    source        TEXT        NOT NULL DEFAULT 'upload', -- 'upload' | 'drive' | 'instagram' | 'seed' | 'csv'
    -- Optional link to the entity this file is about (an artist's portfolio image,
    -- a campaign screenshot, a customer's record). Nullable — a loose upload has none.
    linked_entity_type TEXT   CHECK (linked_entity_type IN ('campaign', 'artist', 'customer') OR linked_entity_type IS NULL),
    linked_entity_id   TEXT,
    -- Type-specific facts kept opaque to the schema: {rows, columns} for a CSV,
    -- {bytes, width, height} for an image, {document_id} pointer for a doc.
    meta          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    active        BOOLEAN     NOT NULL DEFAULT TRUE, -- false = soft-removed (drops from every agent at once)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every read is tenant-scoped and (almost always) active-only; the per-turn
-- inventory counts by (tenant, active, artifact_type).
CREATE INDEX IF NOT EXISTS context_artifacts_tenant_idx
    ON context_artifacts (tenant_id, active, artifact_type);
CREATE INDEX IF NOT EXISTS context_artifacts_linked_idx
    ON context_artifacts (tenant_id, linked_entity_type, linked_entity_id);
