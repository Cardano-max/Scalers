import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { OrchestrationFlow, deriveFlowStages } from '../OrchestrationFlow';
import type { ChatTurn } from '@/lib/data/studio-adapter';

/**
 * PART 3 — orchestration flow. A compact Host → Strategist → Drafts(×N) →
 * Critics(×N) → Jury strip that lights up the stages that have actually run and
 * reports REAL fan-out counts derived from the thread.
 */

const realRunTurns: ChatTurn[] = [
  { id: 'op', role: 'OPERATOR', label: 'You', text: '▶ Run campaign', at: '2026-06-30T10:00:00Z' },
  { id: 'st', role: 'STRATEGIST', label: 'Strategist', text: 'Strategy.', at: '2026-06-30T10:00:01Z' },
  { id: 'd1', role: 'COPYWRITER', label: 'Draft', text: 'Draft 1.', at: '2026-06-30T10:00:02Z' },
  { id: 'd2', role: 'COPYWRITER', label: 'Draft', text: 'Draft 2.', at: '2026-06-30T10:00:03Z' },
  { id: 'd3', role: 'COPYWRITER', label: 'Draft', text: 'Draft 3.', at: '2026-06-30T10:00:04Z' },
  { id: 'c1', role: 'CRITIC', label: 'Critic', text: 'Critique 1.', at: '2026-06-30T10:00:05Z' },
  { id: 'c2', role: 'CRITIC', label: 'Critic', text: 'Critique 2.', at: '2026-06-30T10:00:06Z' },
  { id: 'c3', role: 'CRITIC', label: 'Critic', text: 'Critique 3.', at: '2026-06-30T10:00:07Z' },
  { id: 'ju', role: 'JURY', label: 'Jury', text: 'Verdict.', at: '2026-06-30T10:00:08Z' },
];

describe('deriveFlowStages', () => {
  it('counts the real 8-step run (strategist, draft×3, critic×3, jury)', () => {
    const stages = deriveFlowStages(realRunTurns);
    const byKey = Object.fromEntries(stages.map((s) => [s.key, s]));
    expect(byKey.strategist.done).toBe(true);
    expect(byKey.drafts.done).toBe(true);
    expect(byKey.drafts.count).toBe(3);
    expect(byKey.critics.count).toBe(3);
    expect(byKey.jury.done).toBe(true);
    // host never spoke in this deterministic run -> stays not-done.
    expect(byKey.host.done).toBe(false);
  });

  it('marks all stages not-done for an empty / operator-only thread', () => {
    expect(deriveFlowStages([]).every((s) => !s.done)).toBe(true);
    const opOnly: ChatTurn[] = [
      { id: 'op', role: 'OPERATOR', label: 'You', text: 'hi', at: '2026-06-30T10:00:00Z' },
    ];
    expect(deriveFlowStages(opOnly).every((s) => !s.done)).toBe(true);
  });
});

describe('OrchestrationFlow render', () => {
  it('shows done stages highlighted with their real counts', () => {
    const { container } = render(<OrchestrationFlow turns={realRunTurns} />);
    const drafts = container.querySelector('[data-stage="drafts"]') as HTMLElement;
    expect(drafts.getAttribute('data-done')).toBe('true');
    expect(drafts.textContent).toContain('×3');
    const host = container.querySelector('[data-stage="host"]') as HTMLElement;
    expect(host.getAttribute('data-done')).toBe('false');
    expect(screen.getByLabelText('Campaign orchestration flow')).toBeInTheDocument();
  });
});
