import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { LineageChips } from '../LineageChips';
import { ConsoleProvider, useConsole } from '@/state/console-store';

/**
 * LineageChips renders a campaign/run chip as a LINK only when its id genuinely
 * resolves to a real target. A real run with no campaign shows an explicit honest
 * "no campaign" state; a blank/sentinel id is treated as absent so we never render
 * a chip labelling a campaign/run that does not exist (e.g. `campaign:null`) or a
 * link that resolves nowhere.
 *
 * A <Probe> mirrors the console store's current screen + contextId so we can assert
 * a chip actually navigates to the EXACT id it claims.
 */
function Probe() {
  const c = useConsole();
  return (
    <div data-testid="nav">
      {c.screen}:{c.contextId ?? ''}
    </div>
  );
}

function wrap(node: React.ReactNode) {
  return render(
    <ConsoleProvider>
      {node}
      <Probe />
    </ConsoleProvider>,
  );
}

describe('LineageChips', () => {
  it('renders a clickable campaign link that opens the REAL run when both ids exist', () => {
    wrap(<LineageChips lineage={{ campaignId: 'camp_1', runId: 'run_1' }} />);

    const campaignChip = screen.getByRole('button', { name: 'campaign:camp_1' });
    expect(campaignChip).toBeInTheDocument();
    expect(screen.queryByText('no campaign')).not.toBeInTheDocument();

    fireEvent.click(campaignChip);
    expect(screen.getByTestId('nav')).toHaveTextContent('runs:run_1');
  });

  it('shows the honest "no campaign" state (no fabricated campaign chip) when a real run has no campaign', () => {
    wrap(<LineageChips lineage={{ campaignId: null, runId: 'run_1' }} />);

    expect(screen.getByText('no campaign')).toBeInTheDocument();
    expect(screen.queryByText(/^campaign:/)).not.toBeInTheDocument();

    // The run itself still resolves and links to the exact run.
    const runChip = screen.getByRole('button', { name: 'run:run_1' });
    fireEvent.click(runChip);
    expect(screen.getByTestId('nav')).toHaveTextContent('runs:run_1');
  });

  it('treats a blank or stringified-null campaign id as absent (never `campaign:null`)', () => {
    wrap(<LineageChips lineage={{ campaignId: '   ', runId: 'run_1' }} />);
    expect(screen.queryByText(/campaign:/)).not.toBeInTheDocument();
    expect(screen.getByText('no campaign')).toBeInTheDocument();

    wrap(<LineageChips lineage={{ campaignId: 'null', runId: 'run_2' }} />);
    expect(screen.queryByText('campaign:null')).not.toBeInTheDocument();
  });

  it('does not render a dead run link for a blank or sentinel run id', () => {
    const { container } = wrap(<LineageChips lineage={{ campaignId: null, runId: 'undefined' }} />);
    expect(screen.queryByText(/run:/)).not.toBeInTheDocument();
    // No run context → no campaign chip and no honest "no campaign" pill either.
    expect(screen.queryByText('no campaign')).not.toBeInTheDocument();
    // The component renders nothing for this empty lineage (only the Probe remains).
    expect(container.querySelector('[role="button"]')).toBeNull();
  });
});
