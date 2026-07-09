import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PlannedSteps } from '../PlannedSteps';
import type { PlannedStep } from '@/lib/studio/interview';

/**
 * The dynamic step-selection display (P4). It must be honest: render nothing without a
 * plan, and when there is one, show the mode label, each step's label + reason, the
 * tools for selected steps, and visually distinguish the skipped steps (data-selected).
 */
const steps: PlannedStep[] = [
  {
    id: 'research',
    label: 'Deep web research',
    selected: true,
    reason: 'Operator chose to source new leads from the web',
    tools: ['firecrawl', 'serp'],
  },
  {
    id: 'birthday',
    label: 'Birthday personalization',
    selected: false,
    reason: 'Not a birthday campaign',
    tools: [],
  },
];

describe('PlannedSteps', () => {
  it('renders nothing when there is no plan (absent or empty)', () => {
    const { container } = render(<PlannedSteps steps={undefined} modeLabel="anything" />);
    expect(container.firstChild).toBeNull();
    const { container: c2 } = render(<PlannedSteps steps={[]} modeLabel="anything" />);
    expect(c2.firstChild).toBeNull();
  });

  it('shows the mode label, step labels + reasons, tool chips, and distinguishes skipped', () => {
    render(<PlannedSteps steps={steps} modeLabel="Lead-gen sprint" />);

    expect(screen.getByText(/Plan: Lead-gen sprint/i)).toBeInTheDocument();

    // Selected step: label, reason, and its tools as chips.
    expect(screen.getByText('Deep web research')).toBeInTheDocument();
    expect(screen.getByText(/Operator chose to source new leads/i)).toBeInTheDocument();
    expect(screen.getByText('firecrawl')).toBeInTheDocument();
    expect(screen.getByText('serp')).toBeInTheDocument();

    // Skipped step: label + the reason it was left out.
    expect(screen.getByText('Birthday personalization')).toBeInTheDocument();
    expect(screen.getByText(/Not a birthday campaign/i)).toBeInTheDocument();

    // Visual distinction is assertable via data-selected on each row.
    const selectedRow = document.querySelector('[data-step-id="research"]');
    const skippedRow = document.querySelector('[data-step-id="birthday"]');
    expect(selectedRow?.getAttribute('data-selected')).toBe('true');
    expect(skippedRow?.getAttribute('data-selected')).toBe('false');
  });
});
