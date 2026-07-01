import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { BlueprintBoardPanel } from '../BlueprintBoardPanel';
import type { CampaignBlueprint, ProgressBoard } from '@/lib/studio/blueprint';

// The EXACT real shapes the backend returns (from GET /studio/run/{id}).
const blueprint: CampaignBlueprint = {
  goal: 'win back lapsed clients',
  angle: 'come back',
  run_id: 'team-x',
  targets: { category: 'past-customer-reactivation', scope: 'whole studio', description: 'lapsed', estimated_size: 3 },
  per_channel_quota: { sms: 3 },
  offer_logic: [
    { objection: 'price', offer_code: 'FLOWER15', offer_kind: 'discount', substantiated: true, note: 'x' },
    { objection: 'payment', offer_code: 'SPLIT3', offer_kind: 'payment', substantiated: true, note: 'x' },
  ],
  assumed_dominant_objection: 'price',
  artist_shop_rules: [],
  research_questions: [],
  compliance_constraints: [],
  review_rules: [],
  stop_conditions: { total_quota: 3, per_channel_quota: { sms: 3 }, notes: [] },
  planner_model: 'grounded_rules',
  planner_rationale: 'x',
};

const board: ProgressBoard = {
  run_id: 'team-x',
  run_status: 'running',
  known: ['a', 'b'],
  missing: ['m'],
  leads_total: 3,
  leads_done: 3,
  objections_addressed: ['price'],
  contradictions: [],
  channels_complete: [],
};

describe('BlueprintBoardPanel render (isolated — hangs the test if it infinite-loops)', () => {
  it('renders the real blueprint + board to markup without hanging', () => {
    const html = renderToStaticMarkup(createElement(BlueprintBoardPanel, { blueprint, board }));
    expect(html).toContain('Plan');
    expect(html).toContain('FLOWER15');
  });

  it('renders honestly with null blueprint/board', () => {
    const html = renderToStaticMarkup(createElement(BlueprintBoardPanel, { blueprint: null, board: null }));
    expect(html).toContain('planner has not run');
  });
});
