import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AgencyCanvas } from '../AgencyCanvas';
import { DataProvider } from '@/lib/data/DataProvider';
import { MockAdapter } from '@/lib/data/mock-adapter';
import type { RunState, RunStep } from '@/lib/studio/run-trace';

/**
 * The "Agency at Work" stream is presentation over REAL run data. These tests pin
 * the honesty contract: with no run it shows an idle CTA / not-connected state and
 * NO agent cards; with a real run it renders exactly the landed steps (real persona
 * names) and the operator-vocabulary lane — never a fabricated agent or count.
 */
function wrap(node: React.ReactNode) {
  return render(
    <DataProvider adapter={new MockAdapter()} tenantId="northwind">
      {node}
    </DataProvider>,
  );
}

function step(seq: number, role: string, output: unknown, createdAt: string): RunStep {
  return { seq, role, model: 'gpt-x', input: { brief: 'x' }, output, createdAt };
}

const liveRun: RunState = {
  runId: 'run_abc',
  status: 'running',
  steps: [
    step(1, 'researcher', { sources: [] }, '2026-06-30T10:00:00Z'),
    step(2, 'strategist', { primary_angle: 'reliability' }, '2026-06-30T10:00:02Z'),
    step(3, 'draft', { hook: 'Stay warm', cta: 'Book today' }, '2026-06-30T10:00:04Z'),
  ],
  nPending: 3,
  pending: [],
  archetype: 'outreach',
  error: null,
};

describe('AgencyCanvas — honest empty + not-connected states', () => {
  it('shows the idle CTA and NO agent cards when there is no run', () => {
    const onRun = vi.fn();
    wrap(<AgencyCanvas runState={null} running={false} connected onRunCampaign={onRun} />);
    expect(screen.getByText('The agency is ready')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run campaign' })).toBeInTheDocument();
    // no fabricated timeline / agent rows before a real run.
    expect(screen.queryByText('Timeline')).not.toBeInTheDocument();
  });

  it('shows the honest not-connected state (no Run button) when the backend is down', () => {
    wrap(<AgencyCanvas runState={null} running={false} connected={false} />);
    expect(screen.getByText(/Backend unreachable/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Run campaign' })).not.toBeInTheDocument();
  });
});

describe('AgencyCanvas — result/review transition on completion', () => {
  const completedRun: RunState = {
    runId: 'run_done',
    status: 'completed',
    steps: [
      step(1, 'researcher', { sources: [] }, '2026-06-30T10:00:00Z'),
      step(2, 'jury', { verdict: 'pass', confidence: 0.9 }, '2026-06-30T10:00:08Z'),
    ],
    nPending: 1,
    pending: [
      {
        id: 'act_9',
        channel: 'EMAIL',
        target: 'ada@example.com',
        subject: 'Slots open',
        draft: 'Hi Ada — a slot opened.',
        idempotencyKey: 'idem_9',
        status: 'pending',
      },
    ],
    archetype: 'outreach',
    error: null,
  };

  it('surfaces the per-draft Approve / Reject / Deep-Review once the run completes', () => {
    const onDeep = vi.fn();
    wrap(<AgencyCanvas runState={completedRun} running={false} connected onDeepReview={onDeep} />);
    expect(screen.getByText('Run complete')).toBeInTheDocument();
    expect(screen.getByText('Review the drafts')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Approve and publish' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open deep review' })).toBeInTheDocument();
  });

  it('does NOT show the review surface mid-run (live working mode)', () => {
    wrap(<AgencyCanvas runState={liveRun} running connected />);
    expect(screen.queryByText('Review the drafts')).not.toBeInTheDocument();
  });

  it('deep-links a staged draft to its EXACT Review Queue row (the action id)', () => {
    const onDeep = vi.fn();
    wrap(<AgencyCanvas runState={completedRun} running={false} connected onDeepReview={onDeep} />);
    // The agency→draft link carries the real action id (the deep-link target the
    // Review Queue resolves via resolveSelectedId — never index 0).
    fireEvent.click(screen.getByRole('button', { name: 'Open deep review' }));
    expect(onDeep).toHaveBeenCalledWith('act_9');
  });
});

describe('AgencyCanvas — honest research skipped vs queued', () => {
  const skippedResearchRun: RunState = {
    runId: 'run_skip',
    status: 'completed',
    steps: [
      step(1, 'strategist', { primary_angle: 'reliability' }, '2026-06-30T10:00:00Z'),
      step(2, 'draft', { hook: 'Stay warm', cta: 'Book today' }, '2026-06-30T10:00:02Z'),
      step(3, 'critic', { verdict: 'pass', confidence: 0.9 }, '2026-06-30T10:00:04Z'),
      step(4, 'jury', { verdict: 'pass', confidence: 0.9 }, '2026-06-30T10:00:08Z'),
    ],
    nPending: 1,
    pending: [],
    archetype: 'artist_spotlight',
    error: null,
  };

  it('shows "Deep Research skipped: not required" when a finished run has no researcher step', () => {
    wrap(<AgencyCanvas runState={skippedResearchRun} running={false} connected />);
    // honest skipped wording in the sources rail — not a forever-"queued" placeholder
    expect(screen.getByText(/Deep Research skipped: not required/i)).toBeInTheDocument();
    expect(screen.queryByText('queued')).not.toBeInTheDocument();
  });
});

describe('AgencyCanvas — renders ONLY real landed steps (role-driven, no fixed crew)', () => {
  it('renders the real run header, the landed lanes, HELD count — and NO un-landed lane', () => {
    wrap(<AgencyCanvas runState={liveRun} running connected />);
    // operator-vocabulary header + real HELD count from runState.nPending.
    expect(screen.getByText('Orchestrating now')).toBeInTheDocument();
    expect(screen.getByText('3 HELD for approval')).toBeInTheDocument();
    // lane stages in the operator's words ("Deep research" also titles the sources rail).
    expect(screen.getAllByText('Deep research').length).toBeGreaterThan(0);
    expect(screen.getByText('Copywriters drafting')).toBeInTheDocument();
    // the jury has NOT landed -> its lane is NOT fabricated (role-driven lanes).
    expect(screen.queryByText('Supervising jury evaluating')).not.toBeInTheDocument();
    // the real landed steps appear in the timeline (persona names), three of them.
    expect(screen.getByText('Timeline')).toBeInTheDocument();
    expect(screen.getAllByText('Researcher').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Strategist').length).toBeGreaterThan(0);
    // jury has NOT landed -> no fabricated jury-evaluated evidence card.
    expect(screen.queryByText('Supervising jury · evaluated')).not.toBeInTheDocument();
  });

  it('renders an IG-specific crew (artist_memory / trend_research) exactly as recorded', () => {
    const igRun: RunState = {
      runId: 'run_ig',
      status: 'running',
      steps: [
        step(1, 'artist_memory', { notes: 'style profile' }, '2026-06-30T10:00:00Z'),
        step(2, 'trend_research', { trends: [] }, '2026-06-30T10:00:01Z'),
      ],
      nPending: null,
      pending: [],
      archetype: 'ig_post',
      error: null,
    };
    wrap(<AgencyCanvas runState={igRun} running connected />);
    // The IG crew renders under its own (honest, humanized) names…
    expect(screen.getAllByText('Artist memory').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Trend research').length).toBeGreaterThan(0);
    // …and the email pipeline is NOT painted over it.
    expect(screen.queryByText('Copywriters drafting')).not.toBeInTheDocument();
    expect(screen.queryByText('Strategist planning')).not.toBeInTheDocument();
  });
});
