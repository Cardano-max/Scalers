/**
 * blueprint — FE types + pure view helpers for the P1.5 executable plan + progress board.
 *
 * The engine's GET /studio/run/{id} now returns the planner's `blueprint` (the executable
 * plan built BEFORE drafting) and the durable `board` (structured run-state). These mirror
 * the backend pydantic `model_dump()` shapes (snake_case keys, matching agui.ts's
 * CampaignPlan convention). HONESTY: every field is real backend data; a run with no
 * blueprint/board (e.g. a pre-P1.5 run) yields `null`, which the panel renders as an honest
 * "not planned yet" — nothing is fabricated on the client.
 */

/** One objection -> REAL offer mapping. `offer_code` is null when no substantiated offer
 *  answers the objection (never an invented code). */
export interface OfferRule {
  objection: string;
  offer_code: string | null;
  offer_kind?: string | null;
  substantiated: boolean;
  note?: string;
}

export interface TargetCohort {
  category: string;
  scope: string;
  description: string;
  estimated_size?: number | null;
}

export interface StopConditions {
  total_quota: number;
  per_channel_quota: Record<string, number>;
  stop_on_no_more_leads?: boolean;
  stop_on_contradiction?: boolean;
  notes?: string[];
}

/** The executable plan the planner builds before drafting (mirrors CampaignBlueprint). */
export interface CampaignBlueprint {
  run_id?: string | null;
  goal: string;
  angle: string;
  targets: TargetCohort;
  per_channel_quota: Record<string, number>;
  artist_shop_rules: string[];
  offer_logic: OfferRule[];
  assumed_dominant_objection?: string | null;
  research_questions: string[];
  compliance_constraints: string[];
  review_rules: string[];
  stop_conditions: StopConditions;
  /** The tier the planner actually ran at ("grounded_rules" when no model call happened). */
  planner_model: string;
  planner_rationale?: string;
}

/** The durable structured run-state (mirrors ProgressBoard). */
export interface ProgressBoard {
  run_id?: string | null;
  run_status: string;
  known: string[];
  missing: string[];
  leads_total: number;
  leads_done: number;
  objections_resolved: string[];
  contradictions: string[];
  channels_complete: string[];
}

/** How many objections the plan can answer with a REAL substantiated offer (the rest are
 *  honestly None — no invented discount). A view helper the panel + tests share. */
export function realOfferCount(blueprint: CampaignBlueprint | null | undefined): number {
  if (!blueprint) return 0;
  return blueprint.offer_logic.filter((r) => r.substantiated && !!r.offer_code).length;
}

/** A 0..1 completion ratio from the board (leads_done / leads_total). Honest 0 when the
 *  total is unknown/zero — never a fabricated progress bar. */
export function boardCompletion(board: ProgressBoard | null | undefined): number {
  if (!board || board.leads_total <= 0) return 0;
  return Math.min(1, board.leads_done / board.leads_total);
}

/** True when the planner made a real best-tier (model) call, vs the deterministic core. */
export function plannerUsedModel(blueprint: CampaignBlueprint | null | undefined): boolean {
  return !!blueprint && blueprint.planner_model.startsWith('anthropic:');
}
