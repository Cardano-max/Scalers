import { describe, it, expect } from 'vitest';
import {
  deriveAgencyStages,
  extractResearchSources,
  stepSummaryLine,
  durationBetween,
  personaForRunRole,
} from '../agency';
import type { RunState, RunStep } from '../run-trace';

/**
 * The Agency-at-Work mapping is REAL-data only. Every count, active flag, and
 * handoff-gate timestamp must come from the actual RunState.steps (agent_runs rows).
 * These tests pin that: no fabricated stage is ever "done", counts are real lengths,
 * the active stage is the earliest expected stage with no landed step, and the
 * handoff edge gate (firstCreatedAt) is the real first step's createdAt.
 */
function step(seq: number, role: string, output: unknown, createdAt?: string): RunStep {
  return { seq, role, model: 'gpt-x', input: null, output, createdAt };
}

function runState(steps: RunStep[], status: RunState['status'] = 'running'): RunState {
  return { runId: 'r1', status, steps, nPending: null, pending: [], archetype: 'outreach', error: null };
}

describe('deriveAgencyStages — real counts + active + handoff gate', () => {
  it('derives real ×N fan-out counts and done flags from the run steps', () => {
    const rs = runState([
      step(1, 'researcher', { sources: [] }, '2026-06-30T10:00:00Z'),
      step(2, 'strategist', { primary_angle: 'speed' }, '2026-06-30T10:00:02Z'),
      step(3, 'draft', { hook: 'h1' }, '2026-06-30T10:00:04Z'),
      step(4, 'draft', { hook: 'h2' }, '2026-06-30T10:00:05Z'),
      step(5, 'critic', { verdict: 'pass' }, '2026-06-30T10:00:06Z'),
    ]);
    const byKey = Object.fromEntries(deriveAgencyStages(rs, true).map((s) => [s.key, s]));
    expect(byKey.research.done).toBe(true);
    expect(byKey.strategy.done).toBe(true);
    expect(byKey.drafts.count).toBe(2);
    expect(byKey.critics.count).toBe(1);
    // jury hasn't landed -> not done, and (running) it is the active stage.
    expect(byKey.jury.done).toBe(false);
    expect(byKey.jury.active).toBe(true);
    // exactly one active stage while running.
    expect(deriveAgencyStages(rs, true).filter((s) => s.active)).toHaveLength(1);
  });

  it('gates the handoff edge on the downstream first step createdAt (firstCreatedAt)', () => {
    const stages = deriveAgencyStages(
      runState([step(1, 'researcher', {}, '2026-06-30T10:00:00Z')]),
      true,
    );
    expect(stages.find((s) => s.key === 'research')!.firstCreatedAt).toBe('2026-06-30T10:00:00Z');
    // strategy hasn't landed -> no createdAt to draw its incoming edge.
    expect(stages.find((s) => s.key === 'strategy')!.firstCreatedAt).toBeNull();
  });

  it('marks every stage not-done and none active for an empty / unknown run', () => {
    const stages = deriveAgencyStages(null, false);
    expect(stages.every((s) => !s.done)).toBe(true);
    expect(stages.every((s) => !s.active)).toBe(true);
  });
});

describe('extractResearchSources — real citations only', () => {
  it('pulls real {url,title} citations and ignores entries without a url', () => {
    const steps = [
      step(1, 'researcher', {
        sources: [
          { url: 'https://example.com/a', title: 'A' },
          { title: 'no url' },
          'https://example.com/b',
        ],
      }),
    ];
    const out = extractResearchSources(steps);
    expect(out.map((s) => s.url)).toEqual(['https://example.com/a', 'https://example.com/b']);
    expect(out[0].title).toBe('A');
  });

  it('returns [] when there are no sources (honest empty, never fabricated)', () => {
    expect(extractResearchSources([step(1, 'researcher', { note: 'no web research' })])).toEqual([]);
  });
});

describe('step summary + duration helpers', () => {
  it('summarizes a draft hook/cta and a critic verdict from real output', () => {
    expect(stepSummaryLine(step(1, 'draft', { hook: 'Hi', cta: 'Book now' }))).toContain('Hook: Hi');
    expect(stepSummaryLine(step(2, 'critic', { verdict: 'pass', confidence: 0.9 }))).toContain('Verdict: pass');
  });
  it('computes a human duration between two real createdAt timestamps', () => {
    expect(durationBetween('2026-06-30T10:00:00.000Z', '2026-06-30T10:00:01.800Z')).toBe('1.8s');
    expect(durationBetween(null, '2026-06-30T10:00:01Z')).toBeNull();
  });
  it('maps raw roles to their persona accents', () => {
    expect(personaForRunRole('critic').key).toBe('critic');
    expect(personaForRunRole('jury').key).toBe('jury');
  });
});
