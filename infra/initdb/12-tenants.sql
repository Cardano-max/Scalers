-- ju1.1: tenant registry + server-side TEST-MODE send safety.
-- test_mode=TRUE refuses every send for the tenant at the connector boundary
-- (actions.publish) unless the recipient is on the operator-approved allowlist.
CREATE TABLE IF NOT EXISTS tenants (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    test_mode           BOOLEAN NOT NULL DEFAULT TRUE,
    test_send_allowlist JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
