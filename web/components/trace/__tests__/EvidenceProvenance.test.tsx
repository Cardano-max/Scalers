import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { EvidenceProvenance } from '../EvidenceProvenance';
import { ConsoleProvider } from '@/state/console-store';
import { MockAdapter } from '@/lib/data/mock-adapter';
import type { ActionEvidence } from '@/lib/data/models';

/**
 * EvidenceProvenance surfaces what a staged draft ACTUALLY used as clean chips and
 * cards (never raw JSON). These tests pin the real-only / honest-empty contract:
 * the brand-voice affordance appears only when it was genuinely used; a research
 * link renders per cited source and NONE when the draft cited none ([]); customer
 * facts render when present; and null evidence collapses to one muted line.
 *
 * The footer reuses LineageChips, which reads the console store, so renders are
 * wrapped in a <ConsoleProvider> (mirrors how the real screens mount it).
 */
function fullEvidence(over: Partial<ActionEvidence> = {}): ActionEvidence {
  return {
    actionId: 'act_full',
    runId: 'run_1',
    campaignId: 'camp_1',
    tenantId: 'ladies8391',
    channel: 'gmail',
    target: 'Rae',
    status: 'pending',
    createdBy: { role: 'draft', model: 'anthropic:claude-sonnet-4-6', reasoningSummary: 'Hello' },
    brandVoice: {
      tenantId: 'ladies8391',
      used: true,
      tone: ['warm, direct'],
      structure: ['one idea per line'],
      prefer: ['made for you'],
      ban: ['slay'],
      approvedClaims: ['Woman-owned studio in Austin.'],
      source: 'skills/brand-voice/tenants/ladies8391/brand-dna.md',
    },
    customer: {
      customerId: null,
      name: 'Rae',
      city: 'Austin',
      note: null,
      interest: null,
      lifecycle: null,
      lastTattooStyle: null,
      winBackCandidate: true,
      factsUsed: ['name=Rae', 'city=Austin'],
    },
    leadMemories: [{ text: 'Staged gmail outreach to Rae', kind: 'outreach', createdAt: null }],
    internalNotes: null,
    researchSources: [
      { url: 'https://example.com/a', title: 'Source A', snippet: 'snip a', query: 'q a' },
      { url: 'https://example.com/b', title: 'Source B', snippet: 'snip b', query: 'q b' },
    ],
    toolCalls: [{ name: 'copywriter_email_cell', detail: 'brand-voiced email copy' }],
    criticReview: null,
    jury: { aggregate: 1, decision: 'review', note: 'staged HELD' },
    confidence: null,
    threshold: null,
    confidenceReason: 'Provided-lead outreach',
    reasoningUrl: 'https://langfuse.example/trace/x',
    isRealOnly: true,
    ...over,
  };
}

function wrap(node: React.ReactNode) {
  return render(<ConsoleProvider>{node}</ConsoleProvider>);
}

describe('EvidenceProvenance', () => {
  it('renders the brand-voice affordance when used, and expands its detail on click', () => {
    wrap(<EvidenceProvenance evidence={fullEvidence()} />);
    expect(screen.getByText(/what this draft actually used/i)).toBeInTheDocument();

    const bvButton = screen.getByRole('button', { name: /brand-dna/i });
    expect(bvButton).toBeInTheDocument();
    // collapsed: the doc detail is not shown yet
    expect(
      screen.queryByText('skills/brand-voice/tenants/ladies8391/brand-dna.md'),
    ).not.toBeInTheDocument();

    fireEvent.click(bvButton);
    expect(screen.getByText('warm, direct')).toBeInTheDocument();
    expect(
      screen.getByText('skills/brand-voice/tenants/ladies8391/brand-dna.md'),
    ).toBeInTheDocument();
  });

  it('does NOT render any brand-voice text when brandVoice is null', () => {
    wrap(<EvidenceProvenance evidence={fullEvidence({ brandVoice: null })} />);
    expect(screen.queryByText(/brand-dna/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Brand voice/i)).not.toBeInTheDocument();
  });

  it('renders a clickable anchor for each research source', () => {
    wrap(<EvidenceProvenance evidence={fullEvidence()} />);
    const hrefs = screen.getAllByRole('link').map((l) => l.getAttribute('href'));
    expect(hrefs).toContain('https://example.com/a');
    expect(hrefs).toContain('https://example.com/b');
    expect(screen.getByText('Source A')).toBeInTheDocument();
    expect(screen.getByText('Source B')).toBeInTheDocument();
  });

  it('renders NO research-source link when researchSources is [] (real-only / honest-empty)', () => {
    wrap(<EvidenceProvenance evidence={fullEvidence({ researchSources: [], reasoningUrl: null })} />);
    const hrefs = screen.queryAllByRole('link').map((l) => l.getAttribute('href'));
    expect(hrefs).not.toContain('https://example.com/a');
    expect(screen.queryByText(/Research sources/i)).not.toBeInTheDocument();
  });

  it('renders customer name and city when the customer is present', () => {
    wrap(<EvidenceProvenance evidence={fullEvidence()} />);
    expect(screen.getByText('Rae')).toBeInTheDocument();
    expect(screen.getByText('Austin')).toBeInTheDocument();
  });

  it('renders the honest-empty line when evidence is null (never raw JSON)', () => {
    wrap(<EvidenceProvenance evidence={null} />);
    expect(screen.getByText('No evidence captured for this draft yet.')).toBeInTheDocument();
  });
});

describe('MockAdapter.getActionEvidence', () => {
  it('act_evidence_bare is real-only: no brand voice and no research sources', async () => {
    const ev = await new MockAdapter().getActionEvidence('act_evidence_bare');
    expect(ev).not.toBeNull();
    expect(ev!.researchSources.length).toBe(0);
    expect(ev!.brandVoice).toBeNull();
  });

  it('act_evidence_full has two cited sources and a used brand voice', async () => {
    const ev = await new MockAdapter().getActionEvidence('act_evidence_full');
    expect(ev).not.toBeNull();
    expect(ev!.researchSources.length).toBe(2);
    expect(ev!.brandVoice?.used).toBe(true);
  });

  it('resolves null (honest) for an unknown id', async () => {
    const ev = await new MockAdapter().getActionEvidence('nope');
    expect(ev).toBeNull();
  });
});
