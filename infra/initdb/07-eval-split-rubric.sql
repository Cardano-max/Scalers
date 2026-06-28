-- Admit split='RUBRIC' on gold_example (jury/human-rater anchor corpus, 4jx.12).
-- Per docs/adr/phase-2-eval-spine.md Decision 1. RUBRIC defines the 0-4 anchors
-- for BOTH human raters and jurors; it is disjoint from CALIBRATION/HOLDOUT and,
-- like SMOKE, is NEVER scored as the holdout and NEVER feeds a gate (the eval
-- queries filter to CALIBRATION/HOLDOUT).
--
-- Fresh clusters get RUBRIC directly from 03-eval-kb.sql's CHECK. This migration
-- updates EXISTING clusters, where 03's CREATE TABLE IF NOT EXISTS is a no-op and
-- the old CHECK would still reject 'RUBRIC'. Idempotent (drop-then-add); the
-- inline CHECK from 03 auto-names to gold_example_split_check.

ALTER TABLE gold_example DROP CONSTRAINT IF EXISTS gold_example_split_check;
ALTER TABLE gold_example
    ADD CONSTRAINT gold_example_split_check
    CHECK (split IN ('CALIBRATION', 'HOLDOUT', 'SMOKE', 'RUBRIC'));
