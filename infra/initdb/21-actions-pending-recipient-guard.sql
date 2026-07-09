-- 21-actions-pending-recipient-guard.sql (nmh.11) — one PENDING draft per recipient
-- FOR THE PROVIDED-LEADS PATH ONLY (worker = 'studio_provided_leads').
--
-- The retry bug (worker='studio_provided_leads', studio.agui._execute_provided_leads_sync):
-- each re-launch mints a FRESH run_id (``team-{campaign_id}-{uuid}``), and the
-- exactly-once key (idempotency_key = '{run_id}:{cust_id}', nmh.2) is run-scoped, so
-- re-staging the SAME recipient under a new run_id inserted a NEW pending row = phantom
-- duplicate (observed on ladies8391: the same target pending across 14-16 run_ids). This
-- installs a STRUCTURAL guard so a retry re-staging an already-pending recipient is a
-- no-op: record_pending_action's bare ``ON CONFLICT DO NOTHING`` absorbs the violation
-- and returns the existing pending row's id (see actions/store.py).
--
-- WHY SCOPED TO worker='studio_provided_leads' (NOT all workers):
--   The per-lead research path (worker='studio_agui_research',
--   studio.agui._research_and_stage_sync) already derives a BATCH-STABLE run_id from
--   (session, goal, target-set) (nmh.2), so its retries are idempotent via the
--   idempotency_key AND two DISTINCT campaign goals to the same customer correctly land
--   as two rows (different goal -> different run_id). A blanket (tenant, worker, target)
--   guard would ignore that goal discriminator and SILENTLY collapse the second distinct
--   campaign onto the first (regressing nmh.2 — the exact silent-collision-drop nmh.2
--   fixed, and returning the WRONG-goal draft). Only the provided/team path has the
--   fresh-uuid retry bug and no goal discriminator, so ONLY it needs (and gets) the
--   structural guard. Other producers (studio_agui_research, team/compose, Outreach, …)
--   are intentionally OUT of scope and unaffected.
--
-- Scope: REAL drafts only (is_seeded = false). Demo/seed fixtures (is_seeded = true) are
-- exempt so this never dedupes/deletes seeded console fixtures, and it aligns with the AC
-- (exactly-N real gmail drafts).
--
-- 08-actions.sql now SELF-HEALS the same guard via ensure_schema (it dedupes on a unique
-- violation and retries), so on any DB the engine touches the index is installed without
-- out-of-band surgery — this file is redundant on such DBs. It is retained as the explicit,
-- reportable initdb / one-time manual apply: it DEDUPES pre-existing phantom duplicates
-- (keeping the EARLIEST pending row per recipient; the extras are the bug's never-approved,
-- never-sent duplicate drafts) and DROP+rebuilds the index, so an operator can run it with a
-- reported before/after count. Idempotent: after the first apply there are no duplicates, the
-- DELETE affects zero rows, and CREATE ... IF NOT EXISTS is a no-op.
-- (Keep this predicate byte-identical to infra/initdb/08-actions.sql.)
DELETE FROM actions a
USING (
    SELECT id, row_number() OVER (
        PARTITION BY tenant_id, worker, target
        ORDER BY created_at, id
    ) AS rn
    FROM actions
    WHERE status = 'pending' AND target IS NOT NULL
      AND worker = 'studio_provided_leads'
      AND is_seeded = false
) d
WHERE a.id = d.id AND d.rn > 1;

DROP INDEX IF EXISTS actions_pending_recipient_uniq;
CREATE UNIQUE INDEX IF NOT EXISTS actions_pending_recipient_uniq
    ON actions (tenant_id, worker, target)
    WHERE status = 'pending' AND target IS NOT NULL
      AND worker = 'studio_provided_leads'
      AND is_seeded = false;
