'use client';

/**
 * Tiny async-state hook driving the mandatory loading / empty / error states.
 * Every screen binding goes through this so we NEVER render a blank screen on
 * loading or error — a kkg.4 edge-case requirement.
 */
import { useEffect, useState, useCallback } from 'react';

export type AsyncState<T> = {
  data: T | undefined;
  loading: boolean;
  error: Error | undefined;
  reload: () => void;
};

export function useAsync<T>(
  fn: () => Promise<T>,
  deps: ReadonlyArray<unknown> = [],
): AsyncState<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | undefined>(undefined);
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(undefined);
    fn()
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e : new Error(String(e)));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { data, loading, error, reload };
}

/** True when a loaded collection is empty (drives the empty-state branch). */
export function isEmpty(data: unknown): boolean {
  if (Array.isArray(data)) return data.length === 0;
  return data == null;
}
