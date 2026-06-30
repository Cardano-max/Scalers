import { describe, it, expect } from 'vitest';
import { runNarration } from '../narration';
import type { RunStep } from '../run-trace';

/**
 * Live team narration (#11) — client mirror of engine run_narration. Pins that the
 * narration is a HONEST projection of REAL recorded steps: one line per step, naming
 * the real lead / channel, with failed steps narrated as snags (never as success).
 */
function steps(): RunStep[] {
  return [
    { seq: 0, role: 'strategist', model: null, input: { goal: 'win-back' }, output: { target_angle: 'warm 90-day check-in' } },
    { seq: 1, role: 'researcher', model: null, input: { customer_id: 'c1', name: 'Mia' }, output: { cited: 2 } },
    { seq: 2, role: 'draft', model: null, input: { customer_id: 'c1', channel: 'email' }, output: { hook: '...' } },
    { seq: 3, role: 'critic', model: null, input: { customer_id: 'c1', channel: 'email' }, output: { verdict: 'ship' } },
    { seq: 4, role: 'jury', model: null, input: { n_leads: 1 }, output: { note: '1 draft staged HELD' } },
  ];
}

describe('runNarration', () => {
  it('emits one line per real step, in order, naming the real lead and channel', () => {
    const lines = runNarration(steps());
    expect(lines).toHaveLength(5);
    expect(lines.map((l) => l.role)).toEqual(['strategist', 'researcher', 'draft', 'critic', 'jury']);
    const byRole = Object.fromEntries(lines.map((l) => [l.role, l.line]));
    expect(byRole.strategist).toContain('warm 90-day check-in');
    expect(byRole.researcher).toContain('Mia');
    expect(byRole.draft).toContain('email');
    expect(byRole.critic).toContain('ship');
    expect(lines.every((l) => l.failed === false)).toBe(true);
  });

  it('narrates failed steps honestly as snags', () => {
    const lines = runNarration([
      { seq: 0, role: 'strategist', model: null, input: {}, output: { status: 'failed', error: 'boom' } },
      { seq: 1, role: 'critic', model: null, input: { channel: 'email' }, output: { verdict: 'error' } },
    ]);
    expect(lines[0].failed).toBe(true);
    expect(lines[0].line.toLowerCase()).toContain('snag');
    expect(lines[1].failed).toBe(true);
    expect(lines[1].line.toLowerCase()).toContain('flagged');
  });

  it('narrates nothing for an empty / absent run', () => {
    expect(runNarration([])).toEqual([]);
    expect(runNarration(undefined)).toEqual([]);
  });
});
