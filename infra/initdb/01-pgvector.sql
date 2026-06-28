-- Enable pgvector in the default database on first boot.
-- Files in /docker-entrypoint-initdb.d run once, only when the data
-- volume is empty (i.e. a fresh cluster).
CREATE EXTENSION IF NOT EXISTS vector;

-- Smoke marker so callers can verify init ran. Harmless if it already exists.
DO $$
BEGIN
    RAISE NOTICE 'pgvector extension enabled: %', (SELECT extversion FROM pg_extension WHERE extname = 'vector');
END $$;
