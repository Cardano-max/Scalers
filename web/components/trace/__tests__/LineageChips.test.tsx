import { describe, it, expect, vi, afterEach } from 'vitest';
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

describe('LineageChips — extended provenance label set', () => {
  afterEach(() => vi.restoreAllMocks());

  it('renders the recipient, lead, brand voice and confidence-reason as honest context labels', () => {
    wrap(
      <LineageChips
        lineage={{
          recipient: 'ada@studio.example',
          leadName: 'Ada Lovelace',
          leadId: 'cust_1',
          brandVoice: 'ladies8391',
          confidenceReason: 'jury 0.91 ≥ 0.85; no safety flags',
        }}
      />,
    );
    expect(screen.getByText('to:ada@studio.example')).toBeInTheDocument();
    expect(screen.getByText('lead:Ada Lovelace')).toBeInTheDocument();
    expect(screen.getByText('voice:ladies8391')).toBeInTheDocument();
    // The why-chip is a plain label carrying the full reason in its title.
    const why = screen.getByText(/^why:/);
    expect(why).toHaveAttribute('title', 'jury 0.91 ≥ 0.85; no safety flags');
  });

  it('renders each cited source as a chip that opens the EXACT url in a new tab', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    wrap(
      <LineageChips
        lineage={{
          sources: [
            { url: 'https://inkandiron.example/gallery', title: 'Gallery' },
            { url: 'https://maps.example/place/123', title: null },
          ],
        }}
      />,
    );
    const first = screen.getByRole('button', { name: /inkandiron\.example/ });
    fireEvent.click(first);
    expect(open).toHaveBeenCalledWith('https://inkandiron.example/gallery', '_blank');
  });

  it('collapses extra sources into a +N pill and skips non-http urls', () => {
    wrap(
      <LineageChips
        lineage={{
          sources: [
            { url: 'https://a.example' },
            { url: 'https://b.example' },
            { url: 'https://c.example' },
            { url: 'https://d.example' },
            { url: 'not-a-url' },
          ],
        }}
      />,
    );
    // 4 real http sources -> 3 chips + a "+1 sources" pill; the bad url is dropped.
    expect(screen.getByText('+1 sources')).toBeInTheDocument();
    expect(screen.queryByText(/not-a-url/)).not.toBeInTheDocument();
  });

  it('shows the CSV file/row when present and is honest-missing (no chip) when absent', () => {
    const { rerender } = wrap(
      <LineageChips lineage={{ csvFile: 'tattoo-studio-leads.csv', csvRow: '7' }} />,
    );
    expect(screen.getByText('csv:tattoo-studio-leads.csv · row 7')).toBeInTheDocument();

    rerender(
      <ConsoleProvider>
        <LineageChips lineage={{ recipient: 'x@y.z' }} />
        <Probe />
      </ConsoleProvider>,
    );
    // No CSV data -> no csv chip at all (honest-missing, never a fake/empty chip).
    expect(screen.queryByText(/^csv:/)).not.toBeInTheDocument();
  });
});
