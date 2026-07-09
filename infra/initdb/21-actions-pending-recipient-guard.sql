-- 21-actions-pending-recipient-guard.sql (nmh.11) — one PENDING draft per recipient.
--
-- The retry bug: each re-run minted a fresh run_id, and the exactly-once key
-- (idempotency_key = '{run_id}:{cust_id}', nmh.2) is run-scoped, so re-staging the
-- SAME recipient under a new run_id inserted a NEW pending row = phantom duplicate
-- (observed: the same target pending across 14-16 run_ids). This installs a
-- STRUCTURAL guard so a retry re-staging an already-pending recipient is a no-op:
--   record_pending_action's bare `ON CONFLICT DO NOTHING` absorbs the violation and
--   returns the existing pending row's id (see actions/store.py).
--
-- The guard is worker-scoped so independent producers (studio_provided_leads vs a
-- compose post worker) never collide, and NULL worker/target rows are excluded.
--
-- Deliberately NOT wired into the runtime ensure_schema: the index cannot be built
-- while historical phantom duplicates exist, so this file first DEDUPES them (keeps
-- the EARLIEST pending row per (tenant_id, worker, target); the extras are the bug's
-- never-approved, never-sent duplicate drafts) and then builds the index. Idempotent:
-- after the first apply there are no duplicates, so the DELETE affects zero rows and
-- CREATE ... IF NOT EXISTS is a no-op. initdb applies it on a fresh cluster; on the
-- live cluster it is applied once, explicitly, with a reported before/after count.

-- Scope: REAL drafts only (is_seeded = false). Demo/seed fixtures (is_seeded =
-- true) are intentionally exempt so this guard never dedupes or deletes seeded
-- console fixtures, and it aligns with the AC (exactly-N real gmail drafts).
DELETE FROM actions a
USING (
    SELECT id, row_number() OVER (
        PARTITION BY tenant_id, worker, target
        ORDER BY created_at, id
    ) AS rn
    FROM actions
    WHERE status = 'pending' AND target IS NOT NULL AND worker IS NOT NULL
      AND is_seeded = false
) d
WHERE a.id = d.id AND d.rn > 1;

DROP INDEX IF EXISTS actions_pending_recipient_uniq;
CREATE UNIQUE INDEX IF NOT EXISTS actions_pending_recipient_uniq
    ON actions (tenant_id, worker, target)
    WHERE status = 'pending' AND target IS NOT NULL AND worker IS NOT NULL
      AND is_seeded = false;
