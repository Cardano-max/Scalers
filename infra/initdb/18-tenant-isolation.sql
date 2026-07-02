-- 18-tenant-isolation.sql — multi-tenant isolation hardening (CustomerAcq-fr1.4,
-- blueprint §4.4 "prereq for SDT as tenant #2"). Extends the Row-Level Security
-- backstop (pattern: 03-eval-kb.sql / 06-kb-content.sql) to the runtime PII
-- tables that lacked it, plus the tenant-config redirect pins.
--
-- WHY RLS here: the data-access layer already filters tenant_id, but the audit
-- found the engine connecting as SUPERUSER with RLS on only 4/39 tables. RLS is
-- the DB-level backstop: a NOSUPERUSER connection (scalers_app) with
-- `app.current_tenant` set per request sees/writes ONLY its tenant's rows even
-- if a query forgets the predicate. Superusers bypass RLS by design, so this is
-- inert for the current superuser-connected runtime and only bites once the
-- runtime moves to scalers_app (AC-1 boot guard).
--
-- "customers": there is no literal customers table — customer/contact PII lives
-- in `memories` (subject_type='customer', created by memory/store.py, RLS added
-- there since it is not an initdb table) and `contact_memories`. This migration
-- covers `actions`, `contact_memories`, and the whole suppression PII family
-- (phone numbers / opt-outs), and conditionally `memories` if it already exists.
-- Idempotent: safe on a fresh cluster and re-runs.

-- scalers_app grants for the newly-RLS'd tables (best-effort, mirrors 03).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'scalers_app') THEN
        CREATE ROLE scalers_app LOGIN PASSWORD 'scalers_app';
    END IF;
    GRANT SELECT, INSERT, UPDATE, DELETE ON
        actions, contact_memories, suppression_ledger, consent, send_events,
        delivery_events, carrier_errors
        TO scalers_app;
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'skipping scalers_app grants (insufficient privilege); RLS still enforced';
WHEN undefined_table THEN
    RAISE NOTICE 'skipping some scalers_app grants (table absent on this cluster)';
END $$;

-- Enable + FORCE RLS and a tenant-isolation policy on each table. FORCE so the
-- table owner is subject to RLS too (only a superuser bypasses). The policy is
-- the exact canonical shape: a row is visible/writable only when its tenant_id
-- equals the session's `app.current_tenant`.
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'actions', 'contact_memories', 'suppression_ledger', 'consent',
        'send_events', 'delivery_events', 'carrier_errors'
    ] LOOP
        IF to_regclass(t) IS NOT NULL THEN
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
            EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
            EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_tenant_isolation', t);
            EXECUTE format(
                'CREATE POLICY %I ON %I USING (tenant_id = current_setting(''app.current_tenant'', true))'
                || ' WITH CHECK (tenant_id = current_setting(''app.current_tenant'', true))',
                t || '_tenant_isolation', t
            );
        END IF;
    END LOOP;

    -- memories is created by memory/store.py (not initdb); RLS also lands there,
    -- but cover it here too when it already exists so a re-run is complete.
    IF to_regclass('memories') IS NOT NULL THEN
        BEGIN
            GRANT SELECT, INSERT, UPDATE, DELETE ON memories TO scalers_app;
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE NOTICE 'skipping memories grant (insufficient privilege)';
        END;
        ALTER TABLE memories ENABLE ROW LEVEL SECURITY;
        ALTER TABLE memories FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS memories_tenant_isolation ON memories;
        CREATE POLICY memories_tenant_isolation ON memories
            USING (tenant_id = current_setting('app.current_tenant', true))
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true));
    END IF;
END $$;

-- Redirect PINS (fr1.4 AC-4): tenant-level invariant that BOTH the SMS and Gmail
-- redirects stay on for a pinned tenant regardless of env, so the SDT tenant
-- cannot reach a real recipient until the operator flips a specific campaign
-- live (the t90.4 go-live path — cross-ref, not implemented here). Additive.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS sms_redirect_pinned   boolean NOT NULL DEFAULT false;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS gmail_redirect_pinned boolean NOT NULL DEFAULT false;
