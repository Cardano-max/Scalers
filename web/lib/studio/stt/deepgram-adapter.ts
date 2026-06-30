/**
 * DeepgramSttAdapter — the HIGH-ACCURACY upgrade seam (NOT yet wired).
 *
 * The operator wants best-accuracy speech-to-text. The browser Web Speech API
 * is the now-version; a server STT like Deepgram (or Whisper) is the accuracy
 * upgrade. This file holds the seam so that engine can drop in behind the same
 * SttAdapter interface without touching the chat UI.
 *
 * HONEST-DEGRADE: this adapter is intentionally a stub. It reports
 * isSupported() === false until a real streaming implementation AND a key are
 * provided, and start() emits a clear message naming the EXACT key required:
 *
 *     DEEPGRAM_API_KEY   (Deepgram realtime STT)
 *
 * No fake transcripts, no fake-green. When this is implemented it will open a
 * Deepgram realtime WebSocket, stream mic audio, and forward results through
 * the same SttCallbacks the WebSpeech adapter uses.
 */
import type { SttAdapter, SttCallbacks, SttStartOptions } from './types';

/** Env key that unlocks the Deepgram high-accuracy STT upgrade. */
export const DEEPGRAM_API_KEY_ENV = 'DEEPGRAM_API_KEY';

export class DeepgramSttAdapter implements SttAdapter {
  readonly id = 'deepgram';
  readonly label = 'Deepgram (high-accuracy)';

  constructor(private readonly apiKey?: string | null) {}

  /**
   * Not supported yet. Even when a key is present the streaming client is not
   * implemented, so we honestly stay unsupported rather than pretend. Flip this
   * to `!!this.apiKey` once the realtime client below is built.
   */
  isSupported(): boolean {
    return false;
  }

  start(callbacks: SttCallbacks, _options?: SttStartOptions): void {
    void _options;
    const reason = this.apiKey
      ? 'Deepgram STT is not implemented yet (streaming client pending). Using browser voice input for now.'
      : `Deepgram high-accuracy STT is not configured. Set ${DEEPGRAM_API_KEY_ENV} to enable it.`;
    callbacks.onError?.(reason);
  }

  stop(): void {
    // no-op until the realtime client exists
  }
}
