-- 26-competitor-posts.sql — operator-uploaded competitor posts (Social Growth intel).
--
-- The competitor creative-intelligence layer stores posts the OPERATOR provides
-- (CSV/JSON upload or pasted handles+metrics — no scraping; the official-API fetch
-- lands later with the Meta token). Rows are idempotent on (tenant, url) via a
-- deterministic id, ``metrics`` carries ONLY the numbers actually provided (a
-- missing likes/views column stays absent — never zero-filled), and ``scores`` /
-- ``total_score`` / ``why_it_worked`` hold the deterministic 0–10 breakdown the
-- IG crew molds patterns from (structure/hook/CTA shape only — artwork, wording
-- and offers always stay ours). Runtime twin: studio/competitor_intel.py.

CREATE TABLE IF NOT EXISTS competitor_posts (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    handle        TEXT NOT NULL,
    url           TEXT,
    platform      TEXT,
    caption       TEXT,
    visual_tags   JSONB NOT NULL DEFAULT '[]'::jsonb,
    metrics       JSONB NOT NULL DEFAULT '{}'::jsonb,  -- likes/comments/views/shares/saves AS PROVIDED
    niche         TEXT,
    posted_at     TIMESTAMPTZ,                         -- NULL when the upload gave no parseable date
    scores        JSONB NOT NULL DEFAULT '{}'::jsonb,  -- per-parameter breakdown (None = no data, excluded)
    total_score   NUMERIC,                             -- weighted 0–10; NULL until scored / no scorable data
    why_it_worked TEXT,
    source        TEXT NOT NULL DEFAULT 'upload',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS competitor_posts_tenant_score_idx
    ON competitor_posts (tenant_id, total_score);
