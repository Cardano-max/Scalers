import { describe, it, expect } from 'vitest';
import {
  boardCompletion,
  plannerUsedModel,
  realOfferCount,
  type CampaignBlueprint,
  type ProgressBoard,
} from '../blueprint';

function bp(partial: Partial<CampaignBlueprint> = {}): CampaignBlueprint {
  return {
    goal: 'win back',
    angle: 'come back',
    targets: { category: 'past-customer-reactivation', scope: 'whole studio', description: 'lapsed' },
    per_channel_quota: { sms: 3 },
    artist_shop_rules: [],
    offer_logic: [
      { objection: 'price', offer_code: 'FLOWER15', substantiated: true },
      { objection: 'trust', offer_code: null, substantiated: false },
    ],
    assumed_dominant_objection: 'price',
    research_questions: [],
    compliance_constraints: [],
    review_rules: [],
    stop_conditions: { total_quota: 3, per_channel_quota: { sms: 3 } },
    planner_model: 'anthropic:claude-opus-4-8',
    ...partial,
  };
}

describe('blueprint view helpers', () => {
  it('counts only REAL substantiated offers (never an invented code)', () => {
    expect(realOfferCount(bp())).toBe(1);
    expect(realOfferCount(null)).toBe(0);
    // A non-substantiated code does not count.
    expect(
      realOfferCount(bp({ offer_logic: [{ objection: 'price', offer_code: 'X', substantiated: false }] })),
    ).toBe(0);
  });

  it('plannerUsedModel is true only for a real anthropic model pin', () => {
    expect(plannerUsedModel(bp())).toBe(true);
    expect(plannerUsedModel(bp({ planner_model: 'grounded_rules' }))).toBe(false);
    expect(plannerUsedModel(null)).toBe(false);
  });

  it('boardCompletion is an honest 0..1 ratio (0 when total unknown)', () => {
    const board: ProgressBoard = {
      run_status: 'completed',
      known: [],
      missing: [],
      leads_total: 4,
      leads_done: 2,
      objections_resolved: ['price'],
      contradictions: [],
      channels_complete: [],
    };
    expect(boardCompletion(board)).toBe(0.5);
    expect(boardCompletion({ ...board, leads_total: 0 })).toBe(0);
    expect(boardCompletion(null)).toBe(0);
  });
});
