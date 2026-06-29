import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ReviewScreen } from '../ReviewScreen';
import { DataProvider } from '@/lib/data/DataProvider';
import { MockAdapter } from '@/lib/data/mock-adapter';

function renderReview() {
  return render(
    <DataProvider adapter={new MockAdapter()} tenantId="northwind">
      <ReviewScreen />
    </DataProvider>,
  );
}

describe('ReviewScreen — escalated → human, on the mock adapter spine', () => {
  it('renders filter chips with live counts and the seeded queue', async () => {
    renderReview();
    // 3 seeded escalations: 1 outreach, 1 reply (comment), 1 post
    expect(await screen.findByRole('button', { name: /All\s*3/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Outreach\s*1/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Replies\s*1/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Posts\s*1/ })).toBeInTheDocument();
  });

  it('shows the autonomy decision card for the first selected action', async () => {
    renderReview();
    expect(await screen.findByText('Autonomy decision')).toBeInTheDocument();
    // confidence vs threshold for the first item (conf 0.78 / 0.85)
    expect(screen.getAllByText(/conf 0\.78 \/ 0\.85/).length).toBeGreaterThan(0);
    // per-dimension jury bars
    expect(screen.getByText('Brand voice')).toBeInTheDocument();
    expect(screen.getByText('Appropriateness')).toBeInTheDocument();
    // deterministic gate chips
    expect(screen.getByText('Suppression')).toBeInTheDocument();
    // the idempotency key (unique to the first seeded action)
    expect(screen.getByText('nw:outreach:bayside-pg:c8821')).toBeInTheDocument();
    // outreach → "Approve & send"
    expect(screen.getByRole('button', { name: 'Approve & send' })).toBeInTheDocument();
  });

  it('Approve removes the item, advances selection, and toasts', async () => {
    renderReview();
    const approve = await screen.findByRole('button', { name: 'Approve & send' });
    fireEvent.click(approve);
    // success toast
    expect(await screen.findByText(/Approved & sent/)).toBeInTheDocument();
    // selection advanced to the next item (the IG comment) — its idem key now shows
    expect(await screen.findByText('nw:comment:ig:coastal-eats:r41')).toBeInTheDocument();
    // the approved item is gone from the queue
    expect(screen.queryByText('nw:outreach:bayside-pg:c8821')).not.toBeInTheDocument();
    // All count dropped 3 → 2
    expect(screen.getByRole('button', { name: /All\s*2/ })).toBeInTheDocument();
  });

  it('Edit opens an inline textarea bound to the draft', async () => {
    renderReview();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    const textarea = await screen.findByRole('textbox');
    expect((textarea as HTMLTextAreaElement).value).toMatch(/Hi Renee/);
    expect(screen.getByRole('button', { name: 'Save draft' })).toBeInTheDocument();
  });

  it('the safety-veto post shows a failed gate chip and a publish action', async () => {
    renderReview();
    // switch to the Posts filter, which selects the lone FB post
    fireEvent.click(await screen.findByRole('button', { name: /Posts\s*1/ }));
    // failed deterministic gate is rendered (Pricing claim ✕)
    expect(await screen.findByText('Pricing claim')).toBeInTheDocument();
    // post → "Approve & publish"
    expect(screen.getByRole('button', { name: 'Approve & publish' })).toBeInTheDocument();
  });
});
