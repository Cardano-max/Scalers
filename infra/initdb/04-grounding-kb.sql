-- Phase-2 grounding KB — the GLOBAL practitioner-wisdom partition (bead 1mk.9).
-- Extends the Phase-1 Postgres+pgvector stack and the rvy.2 eval KB
-- (03-eval-kb.sql) — does NOT recreate either. Idempotent: safe on a fresh
-- cluster (initdb) and on re-run (infra/migrate.sh).
--
-- WHY a separate table (not gold_example): gold_example is the eval store —
-- tenant-scoped (RLS), "items under test", per-rater labels, rubric dimensions.
-- Practitioner wisdom is the opposite shape: GLOBAL reference text (no tenant),
-- never under test, retrieved by the writing cells (brand-voice S2, copywriter
-- S5) as few-shot grounding. It is the KNOW-02 grounding partition the ADR
-- forward-references ("the same KB that grounds brand-voice few-shot reuses
-- [the vector column]"); embed dim matches gold_example.embedding (vector(384)).
--
-- VERBATIM is the asset (operator insight): `text` stores the exact human
-- sentence — re-wording reintroduces AI tells. The loader never paraphrases;
-- this table never normalizes `text`.

CREATE EXTENSION IF NOT EXISTS vector;

-- practitioner_wisdom: one row per verbatim practitioner sentence / distilled
-- DO-DON'T rule. GLOBAL — no tenant_id; the niche stays in per-tenant packs,
-- this wisdom is brand-agnostic and shared across all tenants.
CREATE TABLE IF NOT EXISTS practitioner_wisdom (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    partition       text        NOT NULL DEFAULT 'practitioner-wisdom',
    category        text        NOT NULL
                    CHECK (category IN ('general', 'brand-voice', 'hooks-cta',
                                        'research', 'reply', 'outreach', 'do', 'dont')),
    -- testimonial = first-person field quote; curated-skill-description = a
    -- list author's one-line skill summary; operator-note = our own notes;
    -- distilled-rule = a DO/DON'T rule distilled from the quotes. Lets the
    -- writing cells prefer raw human phrasing over distilled guidance.
    kind            text        NOT NULL DEFAULT 'testimonial'
                    CHECK (kind IN ('testimonial', 'curated-skill-description',
                                    'operator-note', 'distilled-rule')),
    -- The asset: EXACT human wording, including original typos/grammar. Never
    -- normalized, never paraphrased.
    text            text        NOT NULL,
    language        text        NOT NULL DEFAULT 'en',
    source          jsonb       NOT NULL DEFAULT '{}',   -- author/thread/subreddit/doc/attribution_raw
    applicability   text,                                -- e.g. "retarget to tattoo-native sources"
    -- sha256 of the verbatim `text`; the natural-key component that makes
    -- re-ingest idempotent (a re-load of the same harvest never dups).
    content_hash    text        NOT NULL,
    embedding       vector(384),
    harvested_at    date,
    created_at      timestamptz NOT NULL DEFAULT now(),
    -- Re-loading the same sentence under the same partition is a no-op rather
    -- than a duplicate row; distinct phrasings (different hash) are all kept.
    CONSTRAINT practitioner_wisdom_natural_key UNIQUE (partition, content_hash)
);

-- Retrieval filters by partition + category; the vector column is ANN-searchable
-- so few-shot grounding fetches the nearest verbatim snippets by cosine.
CREATE INDEX IF NOT EXISTS practitioner_wisdom_cat_idx
    ON practitioner_wisdom (partition, category);
CREATE INDEX IF NOT EXISTS practitioner_wisdom_embedding_idx
    ON practitioner_wisdom USING hnsw (embedding vector_cosine_ops);

-- ── Access ───────────────────────────────────────────────────────────────────
-- GLOBAL reference data: no tenant rows to isolate, so no tenant RLS. The
-- runtime app role (scalers_app, NOSUPERUSER) reads it for grounding; the
-- offline loader writes it as an admin/superuser connection. Best-effort grant
-- (the role exists once 03-eval-kb.sql ran; skip cleanly if privileges are thin).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'scalers_app') THEN
        GRANT SELECT ON practitioner_wisdom TO scalers_app;
    END IF;
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'skipping scalers_app grant on practitioner_wisdom (insufficient privilege)';
END $$;
