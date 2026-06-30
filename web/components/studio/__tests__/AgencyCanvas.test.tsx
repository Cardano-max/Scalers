import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
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
});

describe('AgencyCanvas — renders ONLY real landed steps', () => {
  it('renders the real run header, lane vocabulary, HELD count, and the landed agents', () => {
    wrap(<AgencyCanvas runState={liveRun} running connected />);
    // operator-vocabulary header + real HELD count from runState.nPending.
    expect(screen.getByText('Orchestrating now')).toBeInTheDocument();
    expect(screen.getByText('3 HELD for approval')).toBeInTheDocument();
    // lane stages in the operator's words ("Deep research" also titles the sources rail).
    expect(screen.getAllByText('Deep research').length).toBeGreaterThan(0);
    expect(screen.getByText('Copywriters drafting')).toBeInTheDocument();
    expect(screen.getByText('Supervising jury evaluating')).toBeInTheDocument();
    // the real landed steps appear in the timeline (persona names), three of them.
    expect(screen.getByText('Timeline')).toBeInTheDocument();
    expect(screen.getAllByText('Researcher').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Strategist').length).toBeGreaterThan(0);
    // jury has NOT landed -> no fabricated jury-evaluated evidence card.
    expect(screen.queryByText('Supervising jury · evaluated')).not.toBeInTheDocument();
  });
});
