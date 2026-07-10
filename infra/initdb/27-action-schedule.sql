-- 27-action-schedule.sql — operator-approved deferred publishing (Engine 2).
--
-- Scheduling NEVER bypasses approve-first: an operator schedules a specific
-- pending draft (an explicit approval gesture, like clicking Approve), the row
-- records when + whether the operator authorized a live send, and the scheduler
-- loop publishes it at that time through the SAME approve_and_publish path —
-- exactly-once claim, tenant TEST-MODE gate, allow-list/redirect all intact.
-- Additive + idempotent.

ALTER TABLE actions ADD COLUMN IF NOT EXISTS scheduled_for timestamptz;
ALTER TABLE actions ADD COLUMN IF NOT EXISTS schedule_live boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS actions_due_schedule_idx
    ON actions (status, scheduled_for)
    WHERE scheduled_for IS NOT NULL;
