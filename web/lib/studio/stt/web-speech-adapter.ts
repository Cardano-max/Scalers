/**
 * WebSpeechSttAdapter — the NOW speech-to-text engine.
 *
 * Wraps the browser's Web Speech API (window.SpeechRecognition /
 * window.webkitSpeechRecognition). Zero key, runs entirely in the browser.
 * Accuracy is "good enough to draft a brief"; the high-accuracy upgrade is a
 * server STT (Deepgram/Whisper) behind the same SttAdapter seam.
 *
 * Testability: the recognizer constructor is injected via a factory so unit
 * tests can drive a fake recognizer with no browser. In the browser, the
 * factory is resolved from window. We deliberately do NOT depend on the DOM
 * lib's SpeechRecognition typings (absent in some TS targets) — we declare the
 * minimal shape we use here.
 */
import type { SttAdapter, SttCallbacks, SttStartOptions } from './types';

/** Minimal shape of a single recognition alternative we read. */
export interface SpeechRecognitionAlternativeLike {
  transcript: string;
  confidence?: number;
}

/** Minimal shape of a single recognition result (array-like of alternatives). */
export interface SpeechRecognitionResultLike {
  readonly 0: SpeechRecognitionAlternativeLike;
  readonly length: number;
  readonly isFinal: boolean;
}

/** Minimal shape of the onresult event. */
export interface SpeechRecognitionEventLike {
  readonly resultIndex: number;
  readonly results: ArrayLike<SpeechRecognitionResultLike>;
}

/** Minimal shape of the onerror event. */
export interface SpeechRecognitionErrorEventLike {
  readonly error?: string;
  readonly message?: string;
}

/** Minimal controllable surface of a SpeechRecognition instance. */
export interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  abort?(): void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  onend: (() => void) | null;
  onstart?: (() => void) | null;
}

/** Produces a fresh recognizer instance per session. */
export type SpeechRecognitionFactory = () => SpeechRecognitionLike;

/** Map a raw Web Speech error code to operator-friendly copy. */
export function humanizeSpeechError(code?: string): string {
  switch (code) {
    case 'not-allowed':
    case 'service-not-allowed':
      return 'Microphone permission was blocked. Allow mic access and try again.';
    case 'no-speech':
      return 'No speech detected. Try again and speak clearly.';
    case 'audio-capture':
      return 'No microphone was found. Check your input device.';
    case 'network':
      return 'Network error reaching the speech service. Check your connection.';
    case 'aborted':
      return 'Voice input was stopped.';
    default:
      return code ? `Speech recognition error: ${code}` : 'Speech recognition failed.';
  }
}

/**
 * Resolve the browser's recognizer constructor, or null if the API is absent
 * (SSR, or a browser without Web Speech such as Firefox at time of writing).
 */
export function resolveBrowserRecognizerFactory(): SpeechRecognitionFactory | null {
  if (typeof window === 'undefined') return null;
  const w = window as unknown as {
    SpeechRecognition?: new () => SpeechRecognitionLike;
    webkitSpeechRecognition?: new () => SpeechRecognitionLike;
  };
  const Ctor = w.SpeechRecognition ?? w.webkitSpeechRecognition;
  if (!Ctor) return null;
  return () => new Ctor();
}

export class WebSpeechSttAdapter implements SttAdapter {
  readonly id = 'web-speech';
  readonly label = 'Browser (Web Speech)';

  private readonly factory: SpeechRecognitionFactory | null;
  private active: SpeechRecognitionLike | null = null;

  /**
   * @param factory Inject a recognizer factory (tests/DI). Omit to resolve
   *   the real browser API from window.
   */
  constructor(factory?: SpeechRecognitionFactory | null) {
    this.factory = factory === undefined ? resolveBrowserRecognizerFactory() : factory;
  }

  isSupported(): boolean {
    return this.factory !== null;
  }

  start(callbacks: SttCallbacks, options: SttStartOptions = {}): void {
    if (!this.factory) {
      callbacks.onError?.('Speech recognition is not supported in this browser.');
      return;
    }
    const rec = this.factory();
    rec.lang = options.lang ?? 'en-US';
    rec.continuous = options.continuous ?? false;
    rec.interimResults = options.interimResults ?? true;

    rec.onresult = (event) => {
      const { results, resultIndex } = event;
      for (let i = resultIndex; i < results.length; i++) {
        const result = results[i];
        if (!result) continue;
        const alt = result[0];
        if (!alt) continue;
        callbacks.onResult?.({ transcript: alt.transcript, isFinal: !!result.isFinal });
      }
    };
    rec.onerror = (event) => {
      callbacks.onError?.(humanizeSpeechError(event?.error));
    };
    rec.onend = () => {
      // Only clear if this is still the active session (a newer start() may
      // have replaced it before this end fired).
      if (this.active === rec) this.active = null;
      callbacks.onEnd?.();
    };

    this.active = rec;
    try {
      rec.start();
    } catch (err) {
      // Some browsers throw if start() is called twice in quick succession.
      if (this.active === rec) this.active = null;
      callbacks.onError?.(
        err instanceof Error ? err.message : 'Could not start voice input.',
      );
    }
  }

  stop(): void {
    if (this.active) {
      try {
        this.active.stop();
      } catch {
        // best-effort; onend (if any) will clear state
      }
    }
  }
}
