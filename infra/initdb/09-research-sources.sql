-- 09-research-sources.sql — durable, citable research sources (P0 make-real / slice 3).
--
-- One row per real Firecrawl search hit collected by the REAL research step (the
-- Studio research agent). These rows are the *citable evidence* the research span
-- in runs.steps points at: the synthesis findings cite these URLs, and the console
-- can render WHERE the agent searched (url/title/snippet) + the QUERY that found it.
--
-- HONESTY GATE: every row here comes from a real Firecrawl /v1/search response.
-- The research agent persists exactly what the provider returned — never a
-- fabricated/placeholder URL. An honest-empty research run persists NO rows.
--
-- Pattern mirrors infra/initdb/08-actions.sql (idempotent CREATE TABLE IF NOT
-- EXISTS, applied at container init AND idempotently at runtime via
-- research.sources_store.ensure_schema — the single source of truth for the schema).

CREATE TABLE IF NOT EXISTS research_sources (
    id          TEXT PRIMARY KEY,
    run_id      TEXT        NOT NULL,
    tenant_id   TEXT        NOT NULL,
    query       TEXT        NOT NULL,   -- the real search query that surfaced this source
    url         TEXT        NOT NULL,   -- the real result URL (from Firecrawl, never invented)
    title       TEXT,                   -- result title as returned (nullable)
    snippet     TEXT,                   -- result snippet/description as returned (nullable)
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS research_sources_run_idx    ON research_sources (run_id);
CREATE INDEX IF NOT EXISTS research_sources_tenant_idx ON research_sources (tenant_id);
