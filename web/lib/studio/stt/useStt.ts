'use client';

/**
 * useStt — React binding for the STT adapter seam.
 *
 * Owns a single adapter instance for the component's lifetime, exposes a tiny
 * listening state machine, and forwards FINAL transcripts to the caller (the
 * chat composer appends them to its draft). Interim guesses are surfaced via
 * `interim` for a live caption while speaking but are never committed.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { createSttAdapter, type SttFactoryOptions } from './index';
import type { SttAdapter } from './types';

export interface UseSttResult {
  /** Is voice input usable in this runtime at all? */
  supported: boolean;
  /** Is the recognizer currently capturing? */
  listening: boolean;
  /** Live interim transcript (resets on final/stop); '' when idle. */
  interim: string;
  /** Last terminal error message, or null. */
  error: string | null;
  /** Engine badge label, e.g. 'Browser (Web Speech)'. */
  adapterLabel: string;
  start: () => void;
  stop: () => void;
  toggle: () => void;
}

/**
 * @param onFinalTranscript Called with each FINAL transcript chunk (trimmed,
 *   non-empty). The latest reference is always used (no stale closures).
 * @param options Factory options (engine preference / DI recognizer).
 */
export function useStt(
  onFinalTranscript: (text: string) => void,
  options?: SttFactoryOptions,
): UseSttResult {
  // One adapter for the component lifetime.
  const adapterRef = useRef<SttAdapter | null>(null);
  if (adapterRef.current === null) {
    adapterRef.current = createSttAdapter(options);
  }
  const adapter = adapterRef.current;

  const [supported] = useState(() => adapter.isSupported());
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState('');
  const [error, setError] = useState<string | null>(null);

  // Keep the latest callback without re-creating start().
  const cbRef = useRef(onFinalTranscript);
  useEffect(() => {
    cbRef.current = onFinalTranscript;
  }, [onFinalTranscript]);

  const stop = useCallback(() => {
    adapter.stop();
  }, [adapter]);

  const start = useCallback(() => {
    if (!adapter.isSupported()) {
      setError('Voice input is not supported in this browser. Type your message instead.');
      return;
    }
    setError(null);
    setInterim('');
    setListening(true);
    adapter.start(
      {
        onResult: (r) => {
          if (r.isFinal) {
            const text = r.transcript.trim();
            if (text) cbRef.current(text);
            setInterim('');
          } else {
            setInterim(r.transcript);
          }
        },
        onError: (msg) => {
          setError(msg);
          setListening(false);
          setInterim('');
        },
        onEnd: () => {
          setListening(false);
          setInterim('');
        },
      },
      { interimResults: true },
    );
  }, [adapter]);

  const toggle = useCallback(() => {
    if (listening) stop();
    else start();
  }, [listening, start, stop]);

  // Stop capture if the component unmounts mid-listen.
  useEffect(() => () => adapter.stop(), [adapter]);

  return {
    supported,
    listening,
    interim,
    error,
    adapterLabel: adapter.label,
    start,
    stop,
    toggle,
  };
}
