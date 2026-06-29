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
    // confidence displayed for the first item (78% → pct(0.78))
    expect(screen.getAllByText('78%').length).toBeGreaterThan(0);
    // per-dimension jury bars
    expect(screen.getByText('Brand voice')).toBeInTheDocument();
    expect(screen.getByText('Appropriateness')).toBeInTheDocument();
    // deterministic gate chips
    expect(screen.getByText('Suppression')).toBeInTheDocument();
    // the action id (unique to the first seeded action, rendered in the detail header)
    expect(screen.getByText('act_8f2a1')).toBeInTheDocument();
    // outreach → "Approve & send"
    expect(screen.getByRole('button', { name: 'Approve & send' })).toBeInTheDocument();
  });

  it('Approve removes the item, advances selection, and toasts', async () => {
    renderReview();
    const approve = await screen.findByRole('button', { name: 'Approve & send' });
    fireEvent.click(approve);
    // success toast
    expect(await screen.findByText(/Approved & sent/)).toBeInTheDocument();
    // selection advanced to the next item (the IG comment) — its action id now shows in detail
    expect(await screen.findByText('act_3c7b9')).toBeInTheDocument();
    // the approved item is gone from the queue
    expect(screen.queryByText('act_8f2a1')).not.toBeInTheDocument();
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

  it('an approve→publish that FAILS surfaces the REAL provider error, never a fake success', async () => {
    renderReview();
    // the Instagram comment reply hits the live Graph API (expired token → fails)
    fireEvent.click(await screen.findByRole('button', { name: /Replies\s*1/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Approve & send' }));
    // the verbatim Graph error is rendered in the detail — not paraphrased
    expect(await screen.findByText(/OAuthException/)).toBeInTheDocument();
    expect(screen.getByText(/pages_read_engagement/)).toBeInTheDocument();
    expect(screen.getByText(/Real provider response/)).toBeInTheDocument();
    // it did NOT claim the send succeeded, and the item was not silently dropped
    expect(screen.queryByText(/Approved & sent/)).not.toBeInTheDocument();
    expect(screen.getByText('act_3c7b9')).toBeInTheDocument();
  });
});
