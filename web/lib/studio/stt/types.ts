/**
 * STT adapter seam — speech-to-text for the Campaign Studio chat.
 *
 * This interface is the swap point. The NOW implementation is the browser
 * Web Speech API (zero key, runs in the browser; see web-speech-adapter.ts).
 * A HIGH-ACCURACY backend (Deepgram / Whisper) can drop in behind the same
 * interface once a key is provided — see deepgram-adapter.ts for the seam and
 * the exact key (DEEPGRAM_API_KEY) that unlocks it.
 *
 * HONESTY: every adapter advertises its `id`/`label` so the UI can BADGE which
 * engine produced a transcript (e.g. "Browser (Web Speech)"). No adapter ever
 * fabricates a transcript — it only forwards what the recognizer emits.
 */

/** A single transcript chunk from the recognizer. */
export interface SttResult {
  /** The recognized text for this chunk. */
  transcript: string;
  /** True once the recognizer has committed this chunk (vs. a live interim guess). */
  isFinal: boolean;
}

/** Lifecycle callbacks the caller supplies to `start`. */
export interface SttCallbacks {
  /** Fired for every interim AND final transcript chunk. */
  onResult?: (result: SttResult) => void;
  /** Fired once on a terminal error; the session is over after this. */
  onError?: (message: string) => void;
  /** Fired when capture stops (naturally, on stop(), or after an error). */
  onEnd?: () => void;
}

/** Per-session capture options. */
export interface SttStartOptions {
  /** BCP-47 language tag, e.g. 'en-US'. Defaults to 'en-US'. */
  lang?: string;
  /** Emit live interim guesses before the final commit. Defaults to true. */
  interimResults?: boolean;
  /** Keep listening across pauses. Defaults to false (single utterance). */
  continuous?: boolean;
}

/**
 * The pluggable speech-to-text engine. Implementations: WebSpeechSttAdapter
 * (now) and DeepgramSttAdapter (seam, pending DEEPGRAM_API_KEY).
 */
export interface SttAdapter {
  /** Stable engine id for diagnostics/badging, e.g. 'web-speech' | 'deepgram'. */
  readonly id: string;
  /** Human-readable label for the UI badge, e.g. 'Browser (Web Speech)'. */
  readonly label: string;
  /** Can this engine actually run right now (API present / key configured)? */
  isSupported(): boolean;
  /** Begin capture. If unsupported, must call onError rather than throw. */
  start(callbacks: SttCallbacks, options?: SttStartOptions): void;
  /** Stop capture. Safe to call when idle (no-op). */
  stop(): void;
}
