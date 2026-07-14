import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AgentContributionsPanel } from '../AgentContributionsPanel';
import { DataProvider } from '@/lib/data/DataProvider';
import { MockAdapter } from '@/lib/data/mock-adapter';
import type { ActionContributions } from '@/lib/data/models';

/**
 * Agent contributions panel — per-agent "what I did for THIS draft" from the
 * recorded agent_runs trail. Contract under test:
 *  - a draft WITH a trail renders every recorded entry (agent, output, next-use)
 *  - degraded/idle entries render honestly (status chip), never hidden
 *  - a draft with NO trail renders NOTHING (no invented panel)
 */

const TRAIL: ActionContributions = {
  actionId: 'act_x1',
  runId: 'run_x1',
  customerId: 'cust_x1',
  agentRunCount: 65,
  note: 'Built from the run’s real agent_runs trail.',
  contributions: [
    {
      agent: 'Strategy', model: 'anthropic:test', status: 'done',
      purpose: 'Set the campaign-wide positioning.',
      output: 'Trusted studio, zero pressure',
      nextUse: 'The copywriter writes inside this strategy.',
    },
    {
      agent: 'Identity Guardian', model: 'deterministic:identity-evidence', status: 'done',
      purpose: 'Verify any public profile really is THIS customer.',
      output: '1 confirmed · 0 likely · 3 uncertain (shown, not used) · 2 rejected',
      nextUse: 'Only confirmed/likely facts reached the dossier.',
    },
    {
      agent: 'Location Resolver', model: 'deterministic:on-file-first', status: 'missing',
      purpose: 'Target by the CUSTOMER’s location.',
      output: 'location unknown — not invented; no location-based angle used',
      nextUse: 'Strategy/copy may reference the location only when grounded.',
    },
    {
      agent: 'Analyst', model: 'anthropic:test', status: 'done',
      purpose: 'Classify the REAL objection.',
      output: 'objection: price (stated) · readiness: consideration',
      nextUse: 'The copywriter leads with this objection.',
      personalization: { level: 'high', reason: '8 grounded field(s)' },
      evidence: "that's more than I expected",
    },
  ],
};

function renderPanel(data: ActionContributions | null) {
  const adapter = new MockAdapter();
  adapter.getActionContributions = async () => data;
  return render(
    <DataProvider adapter={adapter} tenantId="northwind">
      <AgentContributionsPanel actionId="act_x1" />
    </DataProvider>,
  );
}

describe('AgentContributionsPanel', () => {
  it('renders every recorded agent entry with its output and hand-off', async () => {
    renderPanel(TRAIL);
    const panel = await screen.findByTestId('agent-contributions');
    expect(panel).toBeInTheDocument();
    expect(screen.getByText(/65 recorded agent steps/)).toBeInTheDocument();
    expect(screen.getByText('Strategy')).toBeInTheDocument();
    expect(screen.getByText('Trusted studio, zero pressure')).toBeInTheDocument();
    expect(screen.getByText(/The copywriter writes inside this strategy/)).toBeInTheDocument();
    // identity counts line survives verbatim — the "not a stranger" proof
    expect(screen.getByText(/3 uncertain \(shown, not used\)/)).toBeInTheDocument();
    // analyst personalization grade + quoted evidence
    expect(screen.getByText('personalization: high')).toBeInTheDocument();
    expect(screen.getByText(/that's more than I expected/)).toBeInTheDocument();
  });

  it('renders honest-missing entries (location unknown) instead of hiding them', async () => {
    renderPanel(TRAIL);
    await screen.findByTestId('agent-contributions');
    expect(screen.getByText(/location unknown — not invented/)).toBeInTheDocument();
    expect(screen.getByTestId('contribution-location-resolver')).toBeInTheDocument();
  });

  it('renders NOTHING when the draft has no recorded trail — no invented panel', async () => {
    renderPanel(null);
    // resolve microtasks: the async read must settle before we assert absence
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId('agent-contributions')).toBeNull();
  });
});
