import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { StagedDraftsReview } from '../StagedDraftsReview';
import { DataProvider } from '@/lib/data/DataProvider';
import type { DataAdapter } from '@/lib/data/adapter';
import type { Action } from '@/lib/data/models';
import type { PendingAction } from '@/lib/studio/run-trace';

/**
 * The result/review surface renders REAL HELD drafts with Approve / Reject / Deep
 * Review. These tests pin: it binds to the given pending rows only (no fabrication),
 * Approve routes the EXISTING approve mutation (id + idempotencyKey), Reject routes
 * reject, Deep Review hands the action id up, a FAILED approve shows WHY (never a
 * fake "sent"), and an empty list renders nothing.
 */
function pending(over: Partial<PendingAction> = {}): PendingAction {
  return {
    id: 'act_1',
    channel: 'EMAIL',
    target: 'ada@example.com',
    subject: 'Spring fine-line slots',
    draft: 'Hi Ada — a few fine-line slots opened up this month.',
    idempotencyKey: 'idem_1',
    status: 'pending',
    ...over,
  };
}

function fakeAdapter(over: Partial<DataAdapter>): DataAdapter {
  return over as unknown as DataAdapter;
}

function wrap(node: React.ReactNode, adapter: DataAdapter) {
  return render(
    <DataProvider adapter={adapter} tenantId="northwind">
      {node}
    </DataProvider>,
  );
}

describe('StagedDraftsReview', () => {
  it('renders one card per real pending draft', () => {
    const adapter = fakeAdapter({ approveAction: vi.fn(), rejectAction: vi.fn() });
    wrap(<StagedDraftsReview pending={[pending(), pending({ id: 'act_2', target: 'lee@x.io' })]} />, adapter);
    expect(screen.getByText('Review the drafts')).toBeInTheDocument();
    expect(screen.getByText('ada@example.com')).toBeInTheDocument();
    expect(screen.getByText('lee@x.io')).toBeInTheDocument();
    expect(screen.getByText('2 HELD')).toBeInTheDocument();
  });

  it('renders nothing for an empty list (no fabrication)', () => {
    const adapter = fakeAdapter({ approveAction: vi.fn(), rejectAction: vi.fn() });
    const { container } = wrap(<StagedDraftsReview pending={[]} />, adapter);
    expect(container.firstChild).toBeNull();
  });

  it('Approve routes the existing approve mutation and flips the card', async () => {
    const approveAction = vi
      .fn()
      .mockResolvedValue({ id: 'act_1', status: 'APPROVED' } as Action);
    const adapter = fakeAdapter({ approveAction, rejectAction: vi.fn() });
    wrap(<StagedDraftsReview pending={[pending()]} />, adapter);

    fireEvent.click(screen.getByRole('button', { name: 'Approve and publish' }));
    await waitFor(() => expect(screen.getByText(/Approved — published/i)).toBeInTheDocument());
    // Default mode is Test (safe): live=false threads through the approve.
    expect(approveAction).toHaveBeenCalledWith('act_1', 'idem_1', false);
  });

  it('Live mode (after confirm) threads live=true into the approve; default is Test', async () => {
    const approveAction = vi
      .fn()
      .mockResolvedValue({ id: 'act_1', status: 'APPROVED', mode: 'live' } as Action);
    const adapter = fakeAdapter({ approveAction, rejectAction: vi.fn() });
    wrap(<StagedDraftsReview pending={[pending()]} />, adapter);

    // Flip to Live -> confirm gate -> Enable Live, then approve.
    fireEvent.click(screen.getByRole('button', { name: 'Live' }));
    fireEvent.click(screen.getByRole('button', { name: 'Enable Live' }));
    fireEvent.click(screen.getByRole('button', { name: 'Approve and publish' }));

    await waitFor(() => expect(approveAction).toHaveBeenCalledWith('act_1', 'idem_1', true));
    // The real mode the engine reported is badged on the sent card — in addition to the
    // toggle's own active-mode badge, so there are now two LIVE badges on screen.
    await screen.findByText(/Approved — published/i);
    expect(screen.getAllByText('LIVE').length).toBeGreaterThanOrEqual(2);
  });

  it('Reject routes the reject mutation and flips the card', async () => {
    const rejectAction = vi.fn().mockResolvedValue({ id: 'act_1', status: 'REJECTED' } as Action);
    const adapter = fakeAdapter({ approveAction: vi.fn(), rejectAction });
    wrap(<StagedDraftsReview pending={[pending()]} />, adapter);

    fireEvent.click(screen.getByRole('button', { name: 'Reject draft' }));
    await waitFor(() => expect(screen.getByText(/Rejected/i)).toBeInTheDocument());
    expect(rejectAction).toHaveBeenCalledWith('act_1');
  });

  it('Deep Review hands the action id up to navigate', () => {
    const onDeepReview = vi.fn();
    const adapter = fakeAdapter({ approveAction: vi.fn(), rejectAction: vi.fn() });
    wrap(<StagedDraftsReview pending={[pending()]} onDeepReview={onDeepReview} />, adapter);
    fireEvent.click(screen.getByRole('button', { name: 'Open deep review' }));
    expect(onDeepReview).toHaveBeenCalledWith('act_1');
  });

  it('a FAILED approve shows WHY and keeps the card (never a fake sent)', async () => {
    const approveAction = vi
      .fn()
      .mockResolvedValue({ id: 'act_1', status: 'FAILED', lastError: 'Graph HTTP 400 #190 expired token' } as Action);
    const adapter = fakeAdapter({ approveAction, rejectAction: vi.fn() });
    wrap(<StagedDraftsReview pending={[pending({ channel: 'INSTAGRAM' })]} />, adapter);

    fireEvent.click(screen.getByRole('button', { name: 'Approve and publish' }));
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('alert').textContent).toContain('expired token');
    // not claimed sent
    expect(screen.queryByText(/Approved — published/i)).not.toBeInTheDocument();
  });
});
