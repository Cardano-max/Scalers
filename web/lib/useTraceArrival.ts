'use client';

/**
 * useTraceArrival — the "animated scroll + highlight ring on arrival" behavior
 * for deep-link landings. When a deep-link resolves to a specific row, the
 * screen calls `trigger(id)`; the hook:
 *   - marks that id as the transient highlight target (for ~1.8s), so the row
 *     renders the `.trace-arrive` teal pulse ring, then
 *   - scrolls that exact row into view (smooth, centered) via the callback ref.
 *
 * This is the visible payoff of the traceability fix: the eye is pulled to the
 * precise related item, never the first row. Reduced-motion is honored in CSS.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

export interface TraceArrival {
  /** The row id currently highlighted (null when nothing just arrived). */
  highlightId: string | null;
  /** Call when a deep-link lands on `id`: highlight + scroll-into-view. */
  trigger: (id: string) => void;
  /** Callback ref the highlighted row attaches so it scrolls into view. */
  scrollRef: (node: HTMLElement | null) => void;
}

export function useTraceArrival(durationMs = 1800): TraceArrival {
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const trigger = useCallback(
    (id: string) => {
      setHighlightId(id);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setHighlightId(null), durationMs);
    },
    [durationMs],
  );

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  // Stable callback ref: fires when the highlighted row mounts (or a row becomes
  // the highlight). Guarded for jsdom/test envs where scrollIntoView is absent.
  const scrollRef = useCallback((node: HTMLElement | null) => {
    if (node && typeof node.scrollIntoView === 'function') {
      node.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, []);

  return { highlightId, trigger, scrollRef };
}
