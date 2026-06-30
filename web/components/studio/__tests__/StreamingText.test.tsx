import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, act } from '@testing-library/react';
import { StreamingText, revealedCount, streamPlan } from '../StreamingText';

/**
 * StreamingText only animates the REVEAL of a real string. These tests pin the
 * contract: it reveals progressively, completes to the EXACT full string, shows a
 * caret only while streaming, and renders the full text immediately when disabled
 * or when prefers-reduced-motion is set (no animation, no caret).
 */

function setReducedMotion(reduced: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: reduced,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

describe('revealedCount / streamPlan (pure reveal math)', () => {
  it('reveals nothing at t=0 and the whole string once enough time passes', () => {
    expect(revealedCount(20, 0)).toBe(0);
    const { totalMs } = streamPlan(20);
    expect(revealedCount(20, totalMs + 1000)).toBe(20);
  });

  it('is monotonic and clamps long strings to a bounded total duration', () => {
    expect(revealedCount(100, 100)).toBeGreaterThanOrEqual(revealedCount(100, 50));
    // A very long string still finishes within the max budget, not length*base.
    const { totalMs } = streamPlan(5000);
    expect(totalMs).toBeLessThanOrEqual(2400);
    expect(revealedCount(5000, 2400)).toBe(5000);
  });
});

describe('StreamingText — progressive reveal', () => {
  beforeEach(() => {
    setReducedMotion(false);
    vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame', 'Date', 'setTimeout', 'clearTimeout'] });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('reveals progressively and completes to the exact full string', () => {
    const full = 'Hook: stay warm all winter';
    const { container } = render(<StreamingText text={full} />);

    // Nothing (or almost nothing) is shown on the first frame...
    expect(container.textContent!.length).toBeLessThan(full.length);

    // ...a little time reveals a partial string with a trailing caret...
    act(() => {
      vi.advanceTimersByTime(80);
    });
    const partial = container.textContent ?? '';
    expect(partial.length).toBeGreaterThan(0);
    expect(partial.length).toBeLessThan(full.length);
    expect(full.startsWith(partial)).toBe(true);
    expect(container.querySelector('.stream-caret')).not.toBeNull();

    // ...and enough time completes it to the verbatim string, caret gone.
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(container.textContent).toBe(full);
    expect(container.querySelector('.stream-caret')).toBeNull();
  });
});

describe('StreamingText — immediate (no animation) paths', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders the full text immediately with no caret when disabled', () => {
    setReducedMotion(false);
    const full = 'Verdict: pass · confidence 0.9';
    const { container } = render(<StreamingText text={full} enabled={false} />);
    expect(container.textContent).toBe(full);
    expect(container.querySelector('.stream-caret')).toBeNull();
  });

  it('respects prefers-reduced-motion: full text immediately, no caret', () => {
    setReducedMotion(true);
    const full = 'Primary angle: reliability when it matters most';
    const { container } = render(<StreamingText text={full} />);
    expect(container.textContent).toBe(full);
    expect(container.querySelector('.stream-caret')).toBeNull();
  });
});
