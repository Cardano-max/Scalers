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
 * The Agency-at-Work mapping is REAL-data + ROLE-DRIVEN. The lanes are EXACTLY the
 * roles that appear in steps[] (order of first appearance): no expected-pipeline
 * skeleton, no channel assumptions, and NO hardcoded agent list — an IG crew
 * (artist_memory / trend_research / …) renders just like the email crew because a
 * lane IS a recorded role. These tests pin that.
 */
function step(seq: number, role: string, output: unknown, createdAt?: string, model = 'gpt-x'): RunStep {
  return { seq, role, model, input: null, output, createdAt };
}

function runState(steps: RunStep[], status: RunState['status'] = 'running'): RunState {
  return { runId: 'r1', status, steps, nPending: null, pending: [], archetype: 'outreach', error: null };
}

describe('deriveAgencyStages — exactly the roles that appear, in first-seen order', () => {
  it('derives one lane per REAL role in order of first appearance, with real ×N counts', () => {
    const rs = runState([
      step(1, 'planner', { blueprint: {} }, '2026-06-30T09:59:58Z', 'anthropic:claude-sonnet-4-5'),
      step(2, 'researcher', { sources: [] }, '2026-06-30T10:00:00Z'),
      step(3, 'strategist', { primary_angle: 'speed' }, '2026-06-30T10:00:02Z'),
      step(4, 'draft', { hook: 'h1' }, '2026-06-30T10:00:04Z'),
      step(5, 'draft', { hook: 'h2' }, '2026-06-30T10:00:05Z'),
      step(6, 'critic', { verdict: 'pass' }, '2026-06-30T10:00:06Z'),
    ]);
    const stages = deriveAgencyStages(rs, true);
    // Every landed role appears — INCLUDING planner (dropped by the old fixed list).
    expect(stages.map((s) => s.key)).toEqual([
      'planner',
      'researcher',
      'strategist',
      'draft',
      'critic',
    ]);
    const byKey = Object.fromEntries(stages.map((s) => [s.key, s]));
    expect(byKey.draft.count).toBe(2);
    expect(byKey.draft.countable).toBe(true);
    expect(byKey.critic.count).toBe(1);
    expect(byKey.critic.countable).toBe(false);
    // Real model surfaces per lane.
    expect(byKey.planner.model).toBe('anthropic:claude-sonnet-4-5');
    // No lane is fabricated for a role that never landed (no jury here).
    expect(stages.some((s) => s.key === 'jury')).toBe(false);
  });

  it('renders an IG-specific crew exactly as recorded — no email pipeline assumed', () => {
    const rs = runState([
      step(1, 'artist_memory', { notes: 'style profile' }, '2026-06-30T10:00:00Z'),
      step(2, 'trend_research', { trends: [] }, '2026-06-30T10:00:01Z'),
      step(3, 'draft', { hook: 'ig caption' }, '2026-06-30T10:00:02Z'),
    ]);
    const stages = deriveAgencyStages(rs, true);
    expect(stages.map((s) => s.key)).toEqual(['artist_memory', 'trend_research', 'draft']);
    const byKey = Object.fromEntries(stages.map((s) => [s.key, s]));
    // Unknown roles get honest humanized labels + a deterministic persona.
    expect(byKey.artist_memory.label).toBe('Artist memory');
    expect(byKey.trend_research.persona.name).toBe('Trend research');
    // And crucially: no strategist/critic/jury lanes were invented.
    expect(stages).toHaveLength(3);
  });

  it('gates the handoff edge on the real first step createdAt (firstCreatedAt)', () => {
    const stages = deriveAgencyStages(
      runState([step(1, 'researcher', {}, '2026-06-30T10:00:00Z')]),
      true,
    );
    expect(stages.find((s) => s.key === 'researcher')!.firstCreatedAt).toBe('2026-06-30T10:00:00Z');
  });

  it('returns [] for an empty / unknown run — never a fabricated skeleton', () => {
    expect(deriveAgencyStages(null, false)).toEqual([]);
    expect(deriveAgencyStages(runState([]), true)).toEqual([]);
  });
});

describe('deriveAgencyStages — honest statuses (done / running / failed only)', () => {
  it('a landed role with an output is done; an in-flight (no-output) latest step is running', () => {
    const rs = runState([
      step(1, 'strategist', { primary_angle: 'speed' }, '2026-06-30T10:00:00Z'),
      step(2, 'draft', null, '2026-06-30T10:00:02Z'), // engine wrote the row on start
    ]);
    const byKey = Object.fromEntries(deriveAgencyStages(rs, true).map((s) => [s.key, s]));
    expect(byKey.strategist.status).toBe('done');
    expect(byKey.draft.status).toBe('running');
    expect(byKey.draft.active).toBe(true);
  });

  it('a landed-but-FAILED strategist/critic reads failed, not done (completed run)', () => {
    const rs = runState(
      [
        step(1, 'strategist', { status: 'failed', error: 'model timeout' }, '2026-06-30T10:00:00Z'),
        step(2, 'draft', { hook: 'h' }, '2026-06-30T10:00:01Z'),
        step(3, 'critic', { verdict: 'approve' }, '2026-06-30T10:00:02Z'),
        step(4, 'critic', { verdict: 'error', rationale: 'critic cell failed: 429' }, '2026-06-30T10:00:03Z'),
        step(5, 'jury', { decision: 'review' }, '2026-06-30T10:00:04Z'),
      ],
      'completed',
    );
    const byKey = Object.fromEntries(deriveAgencyStages(rs, false).map((s) => [s.key, s]));
    expect(byKey.strategist.status).toBe('failed');
    expect(byKey.strategist.done).toBe(false);
    expect(byKey.critic.status).toBe('failed');
    expect(byKey.draft.status).toBe('done');
    expect(byKey.jury.status).toBe('done');
    // Nothing anywhere reads a silent "queued".
    expect(deriveAgencyStages(rs, false).every((s) => (s.status as string) !== 'queued')).toBe(true);
  });
});

describe('personaForRunRole — exact-first, generated for unknown roles', () => {
  it('maps the known roles to their fixed personas', () => {
    expect(personaForRunRole('critic').key).toBe('critic');
    expect(personaForRunRole('jury').key).toBe('jury');
    expect(personaForRunRole('planner').key).toBe('planner');
  });
  it('does NOT collapse crew-specific roles onto the generic researcher', () => {
    expect(personaForRunRole('trend_research').key).toBe('trend_research');
    expect(personaForRunRole('trend_research').name).toBe('Trend research');
  });
  it('is deterministic for the same unknown role', () => {
    expect(personaForRunRole('artist_memory').accent).toBe(personaForRunRole('artist_memory').accent);
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
});
