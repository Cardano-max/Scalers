-- 17-actions-archive.sql — review-queue hygiene: archive-not-delete for the
-- actions table (CustomerAcq-fr1.3, blueprint §4.3-7). NO SILENT DELETES: a
-- stale/dev-run pending action is moved to status='archived' WITH a reason and
-- an archived_at timestamp, and stays queryable forever.
--
-- The `actions` table is owned by the phase3 review-queue (08-actions.sql) and
-- does NOT exist on trunk. This migration is therefore CONDITIONAL: it widens
-- the table only if it is present, so it is a safe no-op on trunk (no phantom
-- table, no conflict with phase3's 08) and the real widen on the phase3 merge.
-- Idempotent: ADD COLUMN IF NOT EXISTS + DROP/ADD of the named status CHECK.

DO $$
BEGIN
    IF to_regclass('actions') IS NOT NULL THEN
        -- Add the 'archived' lifecycle state to the existing named CHECK.
        ALTER TABLE actions DROP CONSTRAINT IF EXISTS actions_status_check;
        ALTER TABLE actions ADD CONSTRAINT actions_status_check CHECK (
            status IN ('pending','approved','sending','sent','rejected','failed','archived'));

        -- WHY the row was archived (dev_run_cleanup | ttl | manual | ...) and WHEN.
        ALTER TABLE actions ADD COLUMN IF NOT EXISTS reason      text;
        ALTER TABLE actions ADD COLUMN IF NOT EXISTS archived_at timestamptz;

        -- Cheap scan of stale pendings to archive (created_at ordered per tenant).
        CREATE INDEX IF NOT EXISTS actions_pending_created_idx
            ON actions (tenant_id, created_at) WHERE status = 'pending';
    END IF;
END $$;
