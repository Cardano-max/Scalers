import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { CountReconciliationPanel } from '../CountReconciliationPanel';
import type { Reconciliation } from '@/lib/studio/run-trace';

/**
 * CountReconciliationPanel (CustomerAcq-sgr) — renders campaign_state.reconciliation
 * verbatim so the panel count equals the review-queue / voice count. 12 requested ->
 * 10 created + 2 skipped (with reasons), reconciled.
 */

const RECON: Reconciliation = {
  requested: 12,
  expected: 12,
  created: 10,
  inQueue: 10,
  approved: 0,
  sent: 0,
  rejected: 0,
  skipped: [
    { row: 3, lead: 'c-x', reason: 'no contact method (no email, phone, handle, or name)' },
    { row: 7, lead: 'c-y', reason: 'not found in database (row did not match a customer)' },
  ],
  failed: [],
  accounted: 12,
  reconciled: true,
};

function tileValue(label: string): string {
  // The label sits in the tile's inner div; its parent is the tile, which also holds
  // the numeric value div (matched by a digits-only regex).
  const tile = screen.getByText(label).parentElement!;
  return within(tile).getByText(/^\d+$/).textContent ?? '';
}

describe('CountReconciliationPanel', () => {
  it('shows requested/created/in-queue/skipped/failed counts from the reconciliation', () => {
    render(<CountReconciliationPanel reconciliation={RECON} />);
    expect(tileValue('Requested')).toBe('12');
    expect(tileValue('Created')).toBe('10');
    expect(tileValue('In queue')).toBe('10');
    expect(tileValue('Skipped')).toBe('2');
    expect(tileValue('Failed')).toBe('0');
  });

  it('shows the reconciled badge and the honest accounting math', () => {
    render(<CountReconciliationPanel reconciliation={RECON} />);
    expect(screen.getByText(/Reconciled/)).toBeInTheDocument();
    expect(screen.getByText(/10 created \+ 2 skipped \+ 0 failed = 12 of 12/)).toBeInTheDocument();
  });

  it('lists the exact per-row skip reasons', () => {
    render(<CountReconciliationPanel reconciliation={RECON} />);
    expect(screen.getByText(/no contact method/)).toBeInTheDocument();
    expect(screen.getByText(/not found in database/)).toBeInTheDocument();
    expect(screen.getByText('row 3')).toBeInTheDocument();
    expect(screen.getByText('row 7')).toBeInTheDocument();
  });

  it('surfaces an undercount honestly instead of a false reconciled badge', () => {
    render(
      <CountReconciliationPanel
        reconciliation={{ ...RECON, skipped: [], failed: [], accounted: 10, reconciled: false }}
      />,
    );
    expect(screen.queryByText(/✓ Reconciled/)).not.toBeInTheDocument();
    expect(screen.getByText(/2 unaccounted/)).toBeInTheDocument();
    expect(screen.getByText(/2 unexplained/)).toBeInTheDocument();
  });

  it('partitions a failed row apart from skips', () => {
    render(
      <CountReconciliationPanel
        reconciliation={{
          ...RECON,
          created: 9,
          skipped: [],
          failed: [{ row: 5, lead: 'c-z', reason: 'draft generation failed: ModelHTTPError' }],
          accounted: 10,
        }}
      />,
    );
    expect(tileValue('Failed')).toBe('1');
    expect(screen.getByText(/draft generation failed/)).toBeInTheDocument();
  });

  it('renders nothing when there is no reconciliation (pre-ledger run)', () => {
    const { container } = render(<CountReconciliationPanel reconciliation={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
