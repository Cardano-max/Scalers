/**
 * Real-data wiring proof. These render the NON-studio tabs against a LIVE-source
 * adapter whose payloads are the REAL engine rows for the demo tenant ladies8391:
 *   - campaign run team-camp_64b774f6f5b4-9c2d4cb0596e (8 traced steps), and
 *   - its 3 pending HOLD drafts (IG / Reels / Email), verbatim from obsapi.
 * Captured from POST http://127.0.0.1:8010/graphql on 2026-06-30. The point is
 * that the data layer + tabs render REAL runs/actions (not the mock seed), that
 * the drafts read as "Will post this caption to Instagram" etc. + route into the
 * Review queue AND Activity, and that NOTHING claims to have sent.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ReviewScreen } from '../ReviewScreen';
import { RunsScreen } from '../RunsScreen';
import { ActivityScreen } from '../ActivityScreen';
import { DataProvider } from '@/lib/data/DataProvider';
import { ConsoleProvider } from '@/state/console-store';
import type { DataAdapter } from '@/lib/data/adapter';
import type { Action, ActivityItem, Run } from '@/lib/data/models';

const TENANT = 'ladies8391';
const RUN_ID = 'team-camp_64b774f6f5b4-9c2d4cb0596e';

function action(over: Partial<Action> & Pick<Action, 'id' | 'channel' | 'subject'>): Action {
  return {
    tenantId: TENANT,
    type: 'POST',
    worker: 'TEAM',
    target: '',
    createdAt: '2026-06-30T10:56:03.318529+00:00',
    context: null,
    draft: 'Real campaign draft body.',
    confidence: 0.9,
    threshold: 0.85,
    escalation: { kind: 'GATE', label: 'approve-first (Phase A)' },
    jury: { confidence: 0.9, threshold: 0.85, agreement: '', dimensions: [], selfConsistency: null, judges: [] },
    gates: [],
    recommendation: null,
    idempotencyKey: `idem-${over.id}`,
    status: 'PENDING',
    lastError: null,
    judges: [],
    isSeeded: false,
    ...over,
  };
}

// The 3 REAL pending drafts of run team-camp_64b774f6f5b4 (status=pending, esc=HOLD).
const REAL_ACTIONS: Action[] = [
  action({ id: 'act_e4643334ea0a47ad', channel: 'IG', subject: 'The Tattoo Starts Before the Needle Does',
    draft: 'She came in with a photo of her mom’s handwriting...' }),
  action({ id: 'act_f0ebffa7645f4569', channel: 'REELS', subject: 'Sketch-to-Skin: This Is What a Custom Consult Actually Looks Like',
    draft: 'Before any sketch, before any stencil: a real conversation.' }),
  action({ id: 'act_b18e7863f87c4471', channel: 'EMAIL', subject: 'Email 1: Here’s What a Custom Consult Actually Looks Like',
    draft: 'Hey. A few consultation spots are opening up this month.' }),
];

// The REAL run with its 8 traced steps (strategist -> 3 drafts -> 3 critics -> jury).
const REAL_RUN: Run = {
  id: RUN_ID,
  tenantId: TENANT,
  type: 'campaign',
  trigger: 'STUDIO',
  status: 'SUCCESS',
  startedAt: '2026-06-30T10:56:03.318529+00:00',
  duration: '0.0s',
  autoCount: 0,
  reviewCount: 8,
  retries: 0,
  idempotencyKey: RUN_ID,
  channels: [],
  trajectory: [],
  note: null,
  traceUrl: null,
  events: [
    { worker: 'STRATEGIST', text: 'strategist: positioning', severity: 'INFO', ms: '—', spans: [], actionId: null, runId: RUN_ID, decisionId: null },
    { worker: 'DRAFT', text: 'draft: The Tattoo Starts Before the Needle Does', severity: 'INFO', ms: '—', spans: [], actionId: null, runId: RUN_ID, decisionId: null },
    { worker: 'DRAFT', text: 'draft: Sketch-to-Skin', severity: 'INFO', ms: '—', spans: [], actionId: null, runId: RUN_ID, decisionId: null },
    { worker: 'DRAFT', text: 'draft: Email 1', severity: 'INFO', ms: '—', spans: [], actionId: null, runId: RUN_ID, decisionId: null },
    { worker: 'CRITIC', text: 'critic: verdict=approve (0.92)', severity: 'INFO', ms: '—', spans: [], actionId: null, runId: RUN_ID, decisionId: null },
    { worker: 'CRITIC', text: 'critic: verdict=approve (0.91)', severity: 'INFO', ms: '—', spans: [], actionId: null, runId: RUN_ID, decisionId: null },
    { worker: 'CRITIC', text: 'critic: verdict=revise (0.88)', severity: 'INFO', ms: '—', spans: [], actionId: null, runId: RUN_ID, decisionId: null },
    { worker: 'JURY', text: 'jury: aggregate=0.90; decision=review', severity: 'INFO', ms: '—', spans: [], actionId: null, runId: RUN_ID, decisionId: null },
  ],
};

/** A LIVE-source adapter returning the real engine rows above. */
function liveFake(): DataAdapter {
  return {
    source: 'live',
    getReviewQueue: async () => REAL_ACTIONS,
    getActivity: async () => [] as ActivityItem[], // nothing sent yet — honest empty
    getRuns: async () => [REAL_RUN],
    getRun: async () => REAL_RUN,
    getActionEvidence: async () => null, // no provenance captured for these rows — honest null
    rejectAction: async (id: string) => REAL_ACTIONS.find((a) => a.id === id)!,
  } as unknown as DataAdapter;
}

function renderWith(node: React.ReactNode) {
  return render(
    <DataProvider adapter={liveFake()} tenantId={TENANT}>
      <ConsoleProvider>{node}</ConsoleProvider>
    </DataProvider>,
  );
}

describe('Review queue — real campaign drafts route in as actionable, HELD cards', () => {
  it('lists the 3 real pending drafts and states the post/send intent per channel', async () => {
    renderWith(<ReviewScreen />);
    // All 3 real drafts present (count chip + the IG one selected by default).
    expect(await screen.findByRole('button', { name: /All\s*3/ })).toBeInTheDocument();
    expect(screen.getAllByText('The Tattoo Starts Before the Needle Does').length).toBeGreaterThan(0);
    // Default selection = first real draft (IG) -> "Will post this caption to Instagram".
    expect(await screen.findByText('Will post this caption to Instagram')).toBeInTheDocument();
    // It is staged + held, and the publish button reads "Approve & publish".
    expect(screen.getByText(/Staged · awaiting approval/)).toBeInTheDocument();
    expect(screen.getByText(/nothing is sent/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Approve & publish' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument();
  });

  it('the Email draft states the send-email intent (channel-correct, not "post")', async () => {
    renderWith(<ReviewScreen />);
    // Select the Email draft by its real subject row.
    (await screen.findByText('Email 1: Here’s What a Custom Consult Actually Looks Like')).click();
    expect(await screen.findByText('Will send this email')).toBeInTheDocument();
  });
});

describe('Runs — each real campaign run drills into its 8 traced steps', () => {
  it('shows the real run id and its strategist/critic/jury steps', async () => {
    renderWith(<RunsScreen />);
    expect((await screen.findAllByText(RUN_ID)).length).toBeGreaterThan(0);
    // The run history renders the 8 real steps' workers.
    expect(screen.getAllByText('STRATEGIST').length).toBeGreaterThan(0);
    expect(screen.getAllByText('JURY').length).toBeGreaterThan(0);
    expect(screen.getByText(/jury: aggregate/)).toBeInTheDocument();
  });
});

describe('Activity — pending drafts also surface here, staged and not sent', () => {
  it('renders the 3 real drafts as staged Activity entries (HELD)', async () => {
    renderWith(<ActivityScreen />);
    // executed activity is empty; the 3 staged drafts fill it instead.
    expect(await screen.findByRole('button', { name: /All\s*3/ })).toBeInTheDocument();
    expect(screen.getAllByText('Staged').length).toBeGreaterThan(0);
    // Default-selected staged draft (IG) shows the intent + the held note, never "Published".
    expect(await screen.findByText('Will post this caption to Instagram')).toBeInTheDocument();
    expect(screen.getByText(/Held for approval — not sent/)).toBeInTheDocument();
    expect(screen.getByText('Draft (staged — not sent)')).toBeInTheDocument();
    expect(screen.queryByText('Published')).not.toBeInTheDocument();
  });
});
