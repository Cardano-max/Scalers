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
    { seq: 0, role: 'strategist', model: null, input: { goal: 'win-back', n_leads: 10 }, output: { target_angle: 'warm 90-day check-in' } },
    { seq: 1, role: 'researcher', model: null, input: { customer_id: 'c1', name: 'Mia' }, output: { cited: 2 } },
    { seq: 2, role: 'draft', model: null, input: { customer_id: 'c1', channel: 'email' }, output: { hook: '...' } },
    { seq: 3, role: 'critic', model: null, input: { customer_id: 'c1', channel: 'email' }, output: { verdict: 'ship' } },
    { seq: 4, role: 'jury', model: null, input: { n_leads: 10 }, output: { note: '1 draft staged HELD' } },
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

  it('carries the REAL "X of N" progress on per-lead roles, none on strategist/jury', () => {
    const byRole = Object.fromEntries(runNarration(steps()).map((l) => [l.role, l.line]));
    expect(byRole.researcher).toContain('1 of 10'); // N = real recorded n_leads
    expect(byRole.draft).toContain('1 of 10');
    expect(byRole.critic).toContain('1 of 10');
    expect(byRole.strategist).not.toContain('of 10');
    expect(byRole.jury).not.toContain('of 10');
  });

  it('counts X up per role and drops "X of N" when the total is genuinely unknown', () => {
    const withTotal = runNarration([
      { seq: 0, role: 'strategist', model: null, input: { n_leads: 2 }, output: { target_angle: 'a' } },
      { seq: 1, role: 'researcher', model: null, input: { name: 'Mia' }, output: { cited: 1 } },
      { seq: 2, role: 'researcher', model: null, input: { name: 'Sam' }, output: { cited: 1 } },
    ]);
    expect(withTotal[1].line).toContain('1 of 2');
    expect(withTotal[2].line).toContain('2 of 2');
    // No recorded total (content campaign) -> no fabricated "X of N".
    const noTotal = runNarration([{ seq: 0, role: 'draft', model: null, input: { channel: 'instagram' }, output: { hook: 'x' } }]);
    expect(noTotal[0].line).not.toMatch(/\d+ of \d+/);
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
