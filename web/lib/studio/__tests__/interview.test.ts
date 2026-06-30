import { describe, it, expect } from 'vitest';
import { deriveInterview, fieldPresent, GATING_FIELDS } from '../interview';
import { emptyPlan, type CampaignPlan } from '../agui';

/**
 * The Agency-page interview GATE (client mirror of engine/studio/interview.py). These
 * pin the arming rule that stops a blind run: a bare/partial plan is NOT armed, every
 * gating field is required, output_count 0 doesn't arm, and an explicit bool false
 * counts as answered (the operator made a choice).
 */
function armedPlan(): CampaignPlan {
  return {
    ...emptyPlan(),
    goal: 'win back lapsed clients',
    audience: "clients who haven't booked in 90 days",
    channels: ['email'],
    lead_source: 'provided',
    campaign_type: 'win-back',
    output_count: 10,
    offer: 'reply to book your next session',
  };
}

describe('deriveInterview — the gate', () => {
  it('an empty plan is not armed and asks the goal first', () => {
    const s = deriveInterview(emptyPlan());
    expect(s.armed).toBe(false);
    expect(s.readyMessage).toBeNull();
    expect(s.nextQuestion?.field).toBe('goal');
    expect(new Set(s.missing)).toEqual(new Set(GATING_FIELDS));
  });

  it('arms only when every gating field is present, then asks the first optional', () => {
    const s = deriveInterview(armedPlan());
    expect(s.armed).toBe(true);
    expect(s.missing).toEqual([]);
    expect(s.readyMessage).toMatch(/go ahead/i);
    expect(s.nextQuestion?.field).toBe('per_lead'); // first optional
  });

  it('the offer / CTA is a gating field that blocks the run until answered', () => {
    expect(GATING_FIELDS).toContain('offer');
    const s = deriveInterview({ ...armedPlan(), offer: '' });
    expect(s.armed).toBe(false);
    expect(s.nextQuestion?.field).toBe('offer');
  });

  it('removing any single gating field disarms it and asks for that field', () => {
    for (const f of GATING_FIELDS) {
      const cleared = f === 'channels' ? [] : f === 'output_count' ? 0 : '';
      const plan = { ...armedPlan(), [f]: cleared } as unknown as Partial<CampaignPlan>;
      const s = deriveInterview(plan);
      expect(s.armed, f).toBe(false);
      expect(s.nextQuestion?.field, f).toBe(f);
    }
  });

  it('output_count 0 does not arm', () => {
    const s = deriveInterview({ ...armedPlan(), output_count: 0 });
    expect(s.armed).toBe(false);
    expect(s.nextQuestion?.field).toBe('output_count');
  });
});

describe('fieldPresent', () => {
  it('treats empty / 0 / [] as unanswered', () => {
    expect(fieldPresent({ channels: [] }, 'channels')).toBe(false);
    expect(fieldPresent({ channels: ['email'] }, 'channels')).toBe(true);
    expect(fieldPresent({ output_count: 0 }, 'output_count')).toBe(false);
    expect(fieldPresent({ output_count: 3 }, 'output_count')).toBe(true);
    expect(fieldPresent({ goal: '   ' }, 'goal')).toBe(false);
    expect(fieldPresent({ goal: 'x' }, 'goal')).toBe(true);
  });

  it('an explicit bool false counts as answered (the operator chose)', () => {
    expect(fieldPresent({ deep_research: false }, 'deep_research')).toBe(true);
    expect(fieldPresent({ deep_research: true }, 'deep_research')).toBe(true);
    expect(fieldPresent({}, 'deep_research')).toBe(false);
  });
});
