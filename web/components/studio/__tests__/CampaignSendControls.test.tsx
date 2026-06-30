import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

/**
 * The campaign-level SAFE-SEND surface. These pin the gates that matter:
 *  - it shows the eligible count + a "Send eligible" button and NEVER a
 *    "send all" / "send everything" affordance,
 *  - clicking "Send eligible" asks for confirmation BEFORE anything is sent,
 *  - the per-draft override Send stays disabled until a reason is typed, and
 *    submitting routes overrideSend(action_id, reason) and notes the audit entry,
 *  - both lists empty renders the honest "nothing staged yet" state.
 * The client module is mocked at its boundary; the component is the unit under test.
 */
vi.mock('@/lib/studio/campaign-send', () => ({
  classifyCampaign: vi.fn(),
  sendEligible: vi.fn(),
  overrideSend: vi.fn(),
}));

import { CampaignSendControls } from '../CampaignSendControls';
import {
  classifyCampaign,
  sendEligible,
  overrideSend,
  type CampaignClassification,
  type CampaignDraft,
} from '@/lib/studio/campaign-send';

const classifyMock = vi.mocked(classifyCampaign);
const sendEligibleMock = vi.mocked(sendEligible);
const overrideMock = vi.mocked(overrideSend);

function draft(over: Partial<CampaignDraft> = {}): CampaignDraft {
  return {
    action_id: 'act_1',
    run_id: 'run_1',
    channel: 'email',
    target: 'ada@example.com',
    worker: 'copy-1',
    conf: 0.42,
    threshold: 0.7,
    esc_kind: null,
    eligible: false,
    reason: 'below confidence bar',
    ...over,
  };
}

function classification(over: Partial<CampaignClassification> = {}): CampaignClassification {
  const eligible =
    over.eligible ??
    [draft({ action_id: 'ok_1', eligible: true, reason: 'clears bar', conf: 0.92, target: 'lee@x.io' })];
  const reviewRequired = over.review_required ?? [draft()];
  return {
    run_id: 'run_1',
    eligible,
    review_required: reviewRequired,
    n_eligible: over.n_eligible ?? eligible.length,
    n_review_required: over.n_review_required ?? reviewRequired.length,
  };
}

describe('CampaignSendControls', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the eligible count + a Send eligible button, and NO send-all/everything affordance', async () => {
    classifyMock.mockResolvedValue(classification());
    render(<CampaignSendControls runId="run_1" />);

    await screen.findByText(/1 draft safe to send/i);
    expect(screen.getByRole('button', { name: /send eligible/i })).toBeInTheDocument();
    // The single most important gate: there is no "send all" / "send everything".
    expect(screen.queryByText(/send (all|everything)/i)).toBeNull();
  });

  it('clicking Send eligible shows a confirm BEFORE calling sendEligible', async () => {
    classifyMock.mockResolvedValue(classification());
    sendEligibleMock.mockResolvedValue({ sent: [], failed: [], skipped: [], n_sent: 1, n_failed: 0, n_skipped: 0 });
    render(<CampaignSendControls runId="run_1" />);

    fireEvent.click(await screen.findByRole('button', { name: /send eligible/i }));
    // Confirm copy appears and nothing has been sent yet.
    expect(screen.getByText(/nothing below the bar is touched/i)).toBeInTheDocument();
    expect(sendEligibleMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /confirm send/i }));
    // Default mode is Test (safe): live=false is passed to the engine.
    await waitFor(() => expect(sendEligibleMock).toHaveBeenCalledWith('run_1', undefined, false));
  });

  it('Live mode passes live=true to sendEligible; Test mode is the default', async () => {
    classifyMock.mockResolvedValue(classification());
    sendEligibleMock.mockResolvedValue({
      sent: [{ action_id: 'ok_1', mode: 'live' }],
      failed: [], skipped: [], n_sent: 1, n_failed: 0, n_skipped: 0,
    });
    render(<CampaignSendControls runId="run_1" />);
    await screen.findByText(/1 draft safe to send/i);

    // Flip to Live, then send.
    fireEvent.click(screen.getByRole('button', { name: /^Live$/ }));
    fireEvent.click(screen.getByRole('button', { name: /send eligible/i }));
    fireEvent.click(screen.getByRole('button', { name: /confirm send/i }));

    await waitFor(() => expect(sendEligibleMock).toHaveBeenCalledWith('run_1', undefined, true));
  });

  it('the override form Send is disabled until a reason is typed', async () => {
    classifyMock.mockResolvedValue(classification());
    render(<CampaignSendControls runId="run_1" />);

    fireEvent.click(await screen.findByRole('button', { name: /open override and send/i }));
    const send = screen.getByRole('button', { name: /override and send draft/i });
    expect(send).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/override reason/i), {
      target: { value: 'VIP client, identity manually verified' },
    });
    expect(send).not.toBeDisabled();
  });

  it('submitting an override routes overrideSend(action_id, reason) and notes the audit', async () => {
    classifyMock.mockResolvedValue(classification());
    overrideMock.mockResolvedValue({
      ok: true,
      action_id: 'act_1',
      was_eligible: false,
      eligibility_reason: 'below confidence bar',
      result: {},
      mode: 'test_redirect',
      last_error: null,
    });
    render(<CampaignSendControls runId="run_1" />);

    fireEvent.click(await screen.findByRole('button', { name: /open override and send/i }));
    fireEvent.change(screen.getByLabelText(/override reason/i), { target: { value: 'manually verified' } });
    fireEvent.click(screen.getByRole('button', { name: /override and send draft/i }));

    // Default Test mode threads live=false through the override too.
    await waitFor(() => expect(overrideMock).toHaveBeenCalledWith('act_1', 'manually verified', undefined, false));
    await screen.findByText(/audit entry was recorded/i);
  });

  it('renders the honest empty state when nothing is staged', async () => {
    classifyMock.mockResolvedValue(
      classification({ eligible: [], review_required: [], n_eligible: 0, n_review_required: 0 }),
    );
    render(<CampaignSendControls runId="run_1" />);

    await screen.findByText(/No drafts staged for this run yet/i);
    expect(screen.queryByRole('button', { name: /send eligible/i })).toBeNull();
  });
});
