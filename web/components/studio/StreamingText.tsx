'use client';

/**
 * StreamingText — a progressive "live generation" reveal of a REAL string.
 *
 * The text content is NEVER fabricated or padded: the full string is supplied by
 * the caller (the real agent reasoning summary) and this only animates how much of
 * it is visible over time, so the active agent's reasoning appears to type itself
 * out as it "thinks". A subtle blinking caret trails the leading edge while it
 * streams, then disappears on completion.
 *
 * Honesty + perf contract:
 *   - The reveal is time-driven (elapsed → revealed count), not one timer per char,
 *     so long reasoning reveals in multi-char frames and never janks; total reveal
 *     time is clamped so big strings still finish quickly.
 *   - Respects prefers-reduced-motion: full text immediately, no caret.
 *   - rAF is cleaned up on unmount; the reveal restarts only when the `text` or
 *     `enabled` inputs change (not on unrelated parent re-renders), so a step that
 *     already finished streaming does not re-stream on the next poll.
 *   - When requestAnimationFrame is unavailable, it falls back to the full text.
 */
import { useEffect, useState } from 'react';

const BASE_MS_PER_CHAR = 16; // ~60 chars/sec base cadence.
const MIN_TOTAL_MS = 220; // very short strings still feel deliberate.
const MAX_TOTAL_MS = 2400; // long reasoning never out-stays its welcome.

/** Total reveal time + per-char budget for a string of `length` chars. Pure. */
export function streamPlan(length: number): { totalMs: number; msPerChar: number } {
  if (length <= 0) return { totalMs: 0, msPerChar: 0 };
  const totalMs = Math.min(MAX_TOTAL_MS, Math.max(MIN_TOTAL_MS, length * BASE_MS_PER_CHAR));
  return { totalMs, msPerChar: totalMs / length };
}

/** How many characters of a `length`-char string are revealed after `elapsedMs`. Pure. */
export function revealedCount(length: number, elapsedMs: number): number {
  if (length <= 0) return 0;
  if (elapsedMs <= 0) return 0;
  const { msPerChar } = streamPlan(length);
  if (msPerChar <= 0) return length;
  return Math.min(length, Math.floor(elapsedMs / msPerChar));
}

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReduced(mq.matches);
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener?.('change', onChange);
    return () => mq.removeEventListener?.('change', onChange);
  }, []);
  return reduced;
}

/**
 * Reveal `text` progressively. Returns the partial string and whether it is done.
 * When disabled / reduced-motion / no rAF, returns the full string immediately.
 */
export function useStreamingText(
  text: string,
  opts: { enabled?: boolean } = {},
): { display: string; done: boolean } {
  const enabled = opts.enabled ?? true;
  const reduced = usePrefersReducedMotion();
  const animate = enabled && !reduced && text.length > 0;

  const [count, setCount] = useState(() => (animate ? 0 : text.length));
  const [done, setDone] = useState(() => !animate);

  useEffect(() => {
    if (!animate) {
      setCount(text.length);
      setDone(true);
      return;
    }
    if (typeof requestAnimationFrame !== 'function') {
      setCount(text.length);
      setDone(true);
      return;
    }

    setCount(0);
    setDone(false);
    const len = text.length;
    const start = Date.now();
    let raf = 0;

    const tick = () => {
      const n = revealedCount(len, Date.now() - start);
      setCount(n);
      if (n >= len) {
        setDone(true);
        return;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);

    return () => {
      if (raf && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(raf);
    };
  }, [text, animate]);

  return { display: text.slice(0, count), done };
}

export interface StreamingTextProps {
  /** The REAL, complete string to reveal. Only its reveal is animated. */
  text: string;
  /** When false, render the full text immediately (e.g. historical/complete steps). */
  enabled?: boolean;
  /** Caret tint (defaults to currentColor / inherited text color). */
  caretColor?: string;
}

/**
 * Renders `text` revealing progressively, with a subtle trailing caret while it
 * streams. The completed string equals `text` exactly.
 */
export function StreamingText({ text, enabled = true, caretColor }: StreamingTextProps) {
  const { display, done } = useStreamingText(text, { enabled });
  return (
    <span>
      {display}
      {!done && (
        <span
          className="stream-caret"
          aria-hidden
          style={caretColor ? { color: caretColor } : undefined}
        />
      )}
    </span>
  );
}
