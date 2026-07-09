-- ju1.1: artist directory (skindesign import). Placeholders stay EMPTY until a
-- later bead fills them from real evidence (never fabricated).
CREATE TABLE IF NOT EXISTS artists (
    id                   TEXT PRIMARY KEY,
    tenant_id            TEXT NOT NULL,
    name                 TEXT NOT NULL,
    email                TEXT,
    phone                TEXT,
    is_test              BOOLEAN NOT NULL DEFAULT FALSE,
    artist_persona       TEXT,
    artist_style_tags    JSONB,
    artist_offer_history JSONB,
    artwork_assets       JSONB,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS artist_studios (
    artist_id   TEXT NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    studio_name TEXT NOT NULL,
    PRIMARY KEY (artist_id, studio_name)
);
