import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { ReviewScreen, groupDraftsByCampaign, campaignLabel } from '../ReviewScreen';
import { DataProvider } from '@/lib/data/DataProvider';
import { ConsoleProvider } from '@/state/console-store';
import { MockAdapter } from '@/lib/data/mock-adapter';
import type { Action } from '@/lib/data/models';

/**
 * Review Queue lineage (CustomerAcq-1ac): drafts grouped by campaign, newest
 * campaign on top, every draft labeled (no orphans), campaign/run chips clickable.
 */

function draft(partial: Partial<Action> & Pick<Action, 'id' | 'createdAt'>): Action {
  return {
    tenantId: 'northwind',
    type: 'OUTREACH',
    channel: 'GMAIL',
    worker: 'OUTREACH',
    target: 'A Lead',
    draft: 'body',
    confidence: 0.8,
    threshold: 0.85,
    escalation: { kind: 'CONFIDENCE', label: 'Below threshold' },
    jury: { confidence: 0.8, threshold: 0.85, agreement: 'split', dimensions: [] },
    gates: [],
    idempotencyKey: `k:${partial.id}`,
    status: 'PENDING',
    ...partial,
  } as Action;
}

describe('groupDraftsByCampaign — newest campaign on top, no orphans', () => {
  it('groups drafts by campaign and orders groups newest-first', () => {
    const items = [
      draft({ id: 'a1', campaignId: 'nw-old', runId: 'run-old', createdAt: '2026-06-29T09:00:00Z' }),
      draft({ id: 'b1', campaignId: 'nw-new', runId: 'run-new', createdAt: '2026-06-29T14:00:00Z' }),
      draft({ id: 'b2', campaignId: 'nw-new', runId: 'run-new', createdAt: '2026-06-29T13:00:00Z' }),
    ];
    const groups = groupDraftsByCampaign(items);
    expect(groups.map((g) => g.campaignId)).toEqual(['nw-new', 'nw-old']); // newest first
    expect(groups[0].drafts.map((d) => d.id)).toEqual(['b1', 'b2']); // newest draft first in group
    expect(groups[0].drafts.length).toBe(2);
  });

  it('collects drafts with no campaign AND no run into one honest group, sorted last', () => {
    const items = [
      draft({ id: 'orphan', createdAt: '2026-06-29T23:59:00Z' }), // newest, but no lineage
      draft({ id: 'c1', campaignId: 'nw-x', runId: 'run-x', createdAt: '2026-06-29T08:00:00Z' }),
    ];
    const groups = groupDraftsByCampaign(items);
    // Even though the orphan is newest, the no-campaign group is sorted LAST.
    expect(groups[groups.length - 1].label).toBe('Unassigned drafts');
    expect(groups[groups.length - 1].drafts.map((d) => d.id)).toEqual(['orphan']);
  });

  it('falls back to the run id as the group when a draft has a run but no campaign', () => {
    const groups = groupDraftsByCampaign([draft({ id: 'r1', runId: 'run-z', createdAt: '2026-06-29T10:00:00Z' })]);
    expect(groups[0].runId).toBe('run-z');
    expect(groups[0].label).toBe('Run run-z');
  });

  it('campaignLabel humanizes a campaign id (drops the tenant token, Title Case)', () => {
    expect(campaignLabel(draft({ id: 'x', campaignId: 'nw-summer-tuneup', createdAt: '2026-06-29T10:00:00Z' }))).toBe('Summer Tuneup');
    expect(campaignLabel(draft({ id: 'y', createdAt: '2026-06-29T10:00:00Z' }))).toBe('Unassigned drafts');
  });

  // CustomerAcq-nmh.2: a freshly research-staged draft now carries a real run_id
  // (engine fix), so it groups as a real campaign/run and sorts NEWEST-FIRST — it is
  // no longer dumped into the always-last "Unassigned" bucket (the "new drafts not
  // clearly on top" symptom). This locks that contract on the render side.
  it('a newest research-staged draft WITH a run id sorts on top, not into Unassigned-last', () => {
    const items = [
      draft({ id: 'older', campaignId: 'nw-holiday', runId: 'run-holiday', createdAt: '2026-07-08T10:00:00Z' }),
      // freshly staged research draft — has a run id (post-fix), newest of all
      draft({ id: 'fresh', runId: 'studio-stage-abc123', createdAt: '2026-07-09T12:00:00Z' }),
    ];
    const groups = groupDraftsByCampaign(items);
    expect(groups[0].drafts.map((d) => d.id)).toEqual(['fresh']); // newest run-group on top
    expect(groups[0].label).not.toBe('Unassigned drafts');
    expect(groups.some((g) => g.label === 'Unassigned drafts')).toBe(false);
  });
});

function renderReview() {
  return render(
    <DataProvider adapter={new MockAdapter()} tenantId="northwind">
      <ConsoleProvider>
        <ReviewScreen />
      </ConsoleProvider>
    </DataProvider>,
  );
}

describe('ReviewScreen lineage rendering — labels + clickable chips', () => {
  it('renders a campaign group header per campaign, newest campaign first', async () => {
    renderReview();
    // The two seeded campaigns both render as group sections.
    const summer = await screen.findByRole('region', { name: /Campaign Summer Tuneup/i });
    const beat = screen.getByRole('region', { name: /Campaign Beat The Heat/i });
    expect(summer).toBeInTheDocument();
    expect(beat).toBeInTheDocument();
    // Newest campaign (Summer tune-up, 13:40) renders BEFORE the older one (11:05).
    expect(summer.compareDocumentPosition(beat) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('the newest campaign group holds its two drafts; the header shows the count + a clickable run chip', async () => {
    renderReview();
    const summer = await screen.findByRole('region', { name: /Campaign Summer Tuneup/i });
    expect(within(summer).getByText(/2 drafts/)).toBeInTheDocument();
    // campaign + run chips are clickable (role=button) deep-links, not dead text.
    const runChip = within(summer).getByRole('button', { name: /run:team-nw-summer-a1b2/ });
    expect(runChip).toBeInTheDocument();
  });

  it('every draft row shows its action/draft id label — no orphan/unlabeled draft', async () => {
    renderReview();
    await screen.findByRole('region', { name: /Campaign Summer Tuneup/i });
    // Each seeded draft's action id is labeled somewhere (its row chip reads
    // `action:<id>`; the selected one also shows in the detail header) — no draft
    // renders without an identity label.
    for (const id of ['act_8f2a1', 'act_3c7b9', 'act_5d1e4']) {
      expect(screen.getAllByText(new RegExp(id)).length).toBeGreaterThan(0);
    }
    // The un-selected drafts prove the ROW chip specifically (they are not in a detail pane).
    expect(screen.getByText('action:act_5d1e4')).toBeInTheDocument();
  });
});
