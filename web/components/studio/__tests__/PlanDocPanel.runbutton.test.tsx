import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { PlanDocPanel } from '../PlanDocPanel';
import type { PlanDoc } from '@/lib/data/studio-adapter';
import type { CampaignPlan } from '@/lib/studio/agui';

/**
 * Guards the deterministic "Run campaign" button contract (the demo-safety upgrade):
 * in the LIVE plan path the panel renders an enabled button that calls onRunCampaign,
 * and while a run is in flight it shows the running state and is disabled (so a run
 * can't be double-fired). The button is the deterministic trigger that does NOT rely
 * on the host model deciding to call the run_campaign tool.
 */

const doc: PlanDoc = {
  id: 'p',
  sessionId: 's',
  version: 1,
  title: 'Plan',
  body: '',
  status: 'draft',
  updatedAt: new Date().toISOString(),
};

const plan: CampaignPlan = {
  goal: 'fill empty Friday slots',
  audience: 'local fine-line fans',
  channels: ['instagram'],
  sections: [],
  tasks_per_role: {},
  assets: [],
  schedule: {},
};

describe('PlanDocPanel — deterministic Run campaign button (live path)', () => {
  it('renders an enabled Run campaign button that fires onRunCampaign', () => {
    const onRun = vi.fn();
    render(
      <PlanDocPanel
        doc={doc}
        body=""
        onChangeBody={() => {}}
        notWired={false}
        plan={plan}
        onRunCampaign={onRun}
        running={false}
        busy={false}
      />,
    );
    const btn = screen.getByRole('button', { name: /Run campaign/i });
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    expect(onRun).toHaveBeenCalledTimes(1);
  });

  it('shows the running state and disables the button while a run is in flight', () => {
    const onRun = vi.fn();
    render(
      <PlanDocPanel
        doc={doc}
        body=""
        onChangeBody={() => {}}
        notWired={false}
        plan={plan}
        onRunCampaign={onRun}
        running={true}
        busy={true}
      />,
    );
    const btn = screen.getByRole('button', { name: /Running the team/i });
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(onRun).not.toHaveBeenCalled();
  });
});
