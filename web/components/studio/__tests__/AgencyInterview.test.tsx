import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AgencyInterview } from '../AgencyInterview';
import type { InterviewState } from '@/lib/studio/interview';

/**
 * The interview panel is the run GATE (P1a): the Run button stays LOCKED until the
 * gate is armed, the current question renders an input, and answering routes through
 * onAnswer. These pin that a blind run is impossible before the interview completes.
 */
const GATING = ['goal', 'audience', 'channels', 'lead_source', 'campaign_type', 'output_count'];

const notArmed: InterviewState = {
  armed: false,
  missing: GATING,
  collected: {},
  nextQuestion: { field: 'goal', question: "What's the goal of this campaign?" },
  readyMessage: null,
  gatingFields: GATING,
};

const armed: InterviewState = {
  armed: true,
  missing: [],
  collected: { goal: 'win back', audience: 'lapsed', channels: ['email'], campaign_type: 'win-back', output_count: 10 },
  nextQuestion: { field: 'action_type', question: 'Outreach, posts, replies, or comments?' },
  readyMessage: "I have enough context. Say 'go ahead' or click Run.",
  gatingFields: GATING,
};

describe('AgencyInterview — gates the run until armed', () => {
  it('disables Run and shows the current question when NOT armed', () => {
    const onRun = vi.fn();
    render(
      <AgencyInterview state={notArmed} busy={false} connected running={false} onAnswer={vi.fn()} onRun={onRun} />,
    );
    expect(screen.getByText("What's the goal of this campaign?")).toBeInTheDocument();
    const run = screen.getByRole('button', { name: 'Run campaign' });
    expect(run).toBeDisabled();
    fireEvent.click(run);
    expect(onRun).not.toHaveBeenCalled();
  });

  it('routes a typed answer through onAnswer(field, value)', () => {
    const onAnswer = vi.fn();
    render(
      <AgencyInterview state={notArmed} busy={false} connected running={false} onAnswer={onAnswer} onRun={vi.fn()} />,
    );
    fireEvent.change(screen.getByLabelText("What's the goal of this campaign?"), {
      target: { value: 'fill Tuesdays' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(onAnswer).toHaveBeenCalledWith('goal', 'fill Tuesdays');
  });

  it('ENABLES Run once armed and clicking it starts the run', () => {
    const onRun = vi.fn();
    render(
      <AgencyInterview state={armed} busy={false} connected running={false} onAnswer={vi.fn()} onRun={onRun} />,
    );
    expect(screen.getByText(/enough context/i)).toBeInTheDocument();
    const run = screen.getByRole('button', { name: 'Run campaign' });
    expect(run).not.toBeDisabled();
    fireEvent.click(run);
    expect(onRun).toHaveBeenCalledTimes(1);
  });

  it('asks LEAD SOURCE as a choice (provided vs source-new) and routes the choice', () => {
    const onAnswer = vi.fn();
    const askLeadSource: InterviewState = {
      ...notArmed,
      nextQuestion: { field: 'lead_source', question: 'Lead source: source NEW leads, or use ONLY your CSV / DB leads?' },
    };
    render(
      <AgencyInterview state={askLeadSource} busy={false} connected running={false} onAnswer={onAnswer} onRun={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Use my CSV / DB leads' }));
    expect(onAnswer).toHaveBeenCalledWith('lead_source', 'provided');
    fireEvent.click(screen.getByRole('button', { name: 'Source new leads (web)' }));
    expect(onAnswer).toHaveBeenCalledWith('lead_source', 'new');
  });

  it('shows the honest not-connected state when disconnected', () => {
    render(
      <AgencyInterview state={null} busy={false} connected={false} running={false} onAnswer={vi.fn()} onRun={vi.fn()} />,
    );
    expect(screen.getByText(/Backend unreachable/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Run campaign' })).not.toBeInTheDocument();
  });
});
