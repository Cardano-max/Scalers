import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { CompetitorPickModal } from '../CompetitorPickModal';
import type { CompetitorSelectionRequest } from '@/lib/studio/run-trace';

/**
 * The competitor pick is presentation over the engine's REAL scraped options (the
 * competitor-research counterpart of the artwork pick). These pin the honesty
 * contract: exactly the engine's options render — @handle, verbatim caption, real
 * metrics/score, the why-it-worked line — clicking one resolves with its REAL id,
 * and a metric the engine did not report never renders (no fabricated numbers).
 */
const request: CompetitorSelectionRequest = {
  kind: 'competitor_pick',
  question: 'Which competitor post should the team mold to your brand?',
  options: [
    {
      id: 'opt_1',
      handle: 'inkrivals',
      caption: 'Fresh fine-line botanical sleeve — healed at 6 weeks and still crisp.',
      url: 'https://instagram.com/p/abc',
      metrics: { likes: 4210, comments: 187 },
      totalScore: 92.5,
      whyItWorked: 'Healed-result proof + a concrete timeframe reads as trust.',
      visualTags: ['fine-line', 'botanical'],
    },
    {
      id: 'opt_2',
      handle: 'rivalstudio',
      caption: 'Walk-in Wednesday: flash sheet drops at noon.',
      url: null,
      metrics: {},
      totalScore: null,
      whyItWorked: null,
      visualTags: [],
    },
  ],
};

describe('CompetitorPickModal — real options, real pick', () => {
  it('renders the question and every engine option with its real fields', () => {
    render(<CompetitorPickModal request={request} onSelect={vi.fn()} />);
    expect(screen.getByText('The run is paused — pick the competitor post to mold')).toBeInTheDocument();
    expect(screen.getByText(/Which competitor post should the team mold/)).toBeInTheDocument();
    // @handle + verbatim caption + real metrics + score + why-it-worked
    expect(screen.getByText('@inkrivals')).toBeInTheDocument();
    expect(screen.getByText(/Fresh fine-line botanical sleeve/)).toBeInTheDocument();
    expect(screen.getByText('4,210 likes · 187 comments')).toBeInTheDocument();
    expect(screen.getByText('score 92.5')).toBeInTheDocument();
    expect(screen.getByText(/Healed-result proof/)).toBeInTheDocument();
    expect(screen.getByText('fine-line')).toBeInTheDocument();
    // the second option renders too — with NO fabricated metrics/score/why
    expect(screen.getByText('@rivalstudio')).toBeInTheDocument();
    expect(screen.getByText(/Walk-in Wednesday/)).toBeInTheDocument();
    expect(screen.getAllByText(/score /)).toHaveLength(1);
    expect(screen.getAllByText(/likes/)).toHaveLength(1);
  });

  it('clicking an option resolves with its REAL option id', () => {
    const onSelect = vi.fn();
    render(<CompetitorPickModal request={request} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole('button', { name: 'Pick competitor post opt_2' }));
    expect(onSelect).toHaveBeenCalledWith('opt_2');
    // a second click while resolving must NOT double-fire
    fireEvent.click(screen.getByRole('button', { name: 'Pick competitor post opt_1' }));
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it('"Decide later" only hides the dialog (the run stays paused server-side)', () => {
    const onDismiss = vi.fn();
    const onSelect = vi.fn();
    render(<CompetitorPickModal request={request} onSelect={onSelect} onDismiss={onDismiss} />);
    fireEvent.click(screen.getByRole('button', { name: 'Decide later' }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(onSelect).not.toHaveBeenCalled();
  });
});
