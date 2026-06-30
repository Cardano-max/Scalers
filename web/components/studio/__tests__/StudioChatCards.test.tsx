import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StudioChatPanel } from '../StudioChatPanel';
import { studioPersona, AGENT_PERSONAS, OPERATOR_PERSONA } from '@/lib/studio/persona';
import type { ChatTurn } from '@/lib/data/studio-adapter';

/**
 * PART 1 — distinct per-agent cards. Proves the conversation renders each speaker
 * as its OWN visually-distinct card: the operator is a right-aligned "You" bubble,
 * and agents that collapse onto the same coarse role (Funnel Architect vs
 * Strategist; Draft vs Copywriter) still get DISTINCT personas/colours from their
 * label. This is the fix for "a grey mess — can't tell who said what".
 */

describe('studioPersona — label-first identity', () => {
  it('maps the operator to the right-aligned You persona', () => {
    expect(studioPersona({ role: 'OPERATOR', label: 'You' })).toBe(OPERATOR_PERSONA);
    expect(OPERATOR_PERSONA.side).toBe('right');
  });

  it('separates agents that share a coarse role via their label', () => {
    // funnel_architect and strategist both map to StudioRole STRATEGIST...
    expect(studioPersona({ role: 'STRATEGIST', label: 'Funnel Architect' })).toBe(
      AGENT_PERSONAS.funnel,
    );
    expect(studioPersona({ role: 'STRATEGIST', label: 'Strategist' })).toBe(
      AGENT_PERSONAS.strategist,
    );
    // ...and draft + copywriter both map to COPYWRITER.
    expect(studioPersona({ role: 'COPYWRITER', label: 'Draft' })).toBe(AGENT_PERSONAS.draft);
    expect(studioPersona({ role: 'COPYWRITER', label: 'Copywriter' })).toBe(
      AGENT_PERSONAS.copywriter,
    );
    // distinct accents — the whole point.
    expect(AGENT_PERSONAS.funnel.accent).not.toBe(AGENT_PERSONAS.strategist.accent);
    expect(AGENT_PERSONAS.draft.accent).not.toBe(AGENT_PERSONAS.copywriter.accent);
  });

  it('matches host, critic and jury labels, and falls back to role', () => {
    expect(studioPersona({ role: 'SYSTEM', label: 'Studio Host' })).toBe(AGENT_PERSONAS.host);
    expect(studioPersona({ role: 'CRITIC', label: 'Critic' })).toBe(AGENT_PERSONAS.critic);
    expect(studioPersona({ role: 'JURY', label: 'Jury' })).toBe(AGENT_PERSONAS.jury);
    // unknown label -> role fallback (no crash, never the operator persona).
    expect(studioPersona({ role: 'SYSTEM', label: 'whatever' })).toBe(AGENT_PERSONAS.system);
  });
});

describe('StudioChatPanel — distinct cards render', () => {
  const turns: ChatTurn[] = [
    { id: '1', role: 'OPERATOR', label: 'You', text: 'Launch summer promo', at: '2026-06-30T10:00:00Z' },
    { id: '2', role: 'SYSTEM', label: 'Studio Host', text: 'On it — who is the audience?', at: '2026-06-30T10:00:05Z' },
    { id: '3', role: 'STRATEGIST', label: 'Funnel Architect', text: 'Top-of-funnel reach.', at: '2026-06-30T10:00:10Z' },
    { id: '4', role: 'COPYWRITER', label: 'Draft', text: 'Draft copy A.', at: '2026-06-30T10:00:15Z' },
    { id: '5', role: 'CRITIC', label: 'Critic', text: 'Hook is weak.', at: '2026-06-30T10:00:20Z' },
  ];

  it('renders one card per turn with its persona label and the operator on the right', () => {
    const { container } = render(
      <StudioChatPanel turns={turns} onSend={() => {}} streamStatus="open" />,
    );
    // every speaker label is visible — no more anonymous grey text.
    expect(screen.getByText('Studio Host')).toBeInTheDocument();
    expect(screen.getByText('Funnel Architect')).toBeInTheDocument();
    expect(screen.getByText('Draft')).toBeInTheDocument();
    expect(screen.getByText('Critic')).toBeInTheDocument();
    expect(screen.getByText('You')).toBeInTheDocument();
    expect(screen.getByText('Launch summer promo')).toBeInTheDocument();

    // distinct personas are tagged on the cards (drives the per-role colour).
    const personas = Array.from(container.querySelectorAll('[data-persona]')).map((el) =>
      el.getAttribute('data-persona'),
    );
    expect(personas).toEqual(['operator', 'host', 'funnel', 'draft', 'critic']);

    // operator card is the right-aligned one; agents are left-aligned.
    const operatorCard = container.querySelector('[data-persona="operator"]') as HTMLElement;
    const hostCard = container.querySelector('[data-persona="host"]') as HTMLElement;
    expect(operatorCard.style.alignSelf).toBe('flex-end');
    expect(hostCard.style.alignSelf).toBe('flex-start');
  });
});
