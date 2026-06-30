/**
 * STT factory + barrel — the single place the chat UI asks for a speech engine.
 *
 * Selection is the swap point for the high-accuracy upgrade. Today `auto`
 * resolves to the browser Web Speech engine because the Deepgram adapter is an
 * honest-degrade stub (isSupported() === false until DEEPGRAM_API_KEY + a
 * streaming client land). When that engine is implemented, `auto` will prefer
 * it automatically with no UI change.
 */
import type { SttAdapter } from './types';
import { WebSpeechSttAdapter, type SpeechRecognitionFactory } from './web-speech-adapter';
import { DeepgramSttAdapter, DEEPGRAM_API_KEY_ENV } from './deepgram-adapter';

export type { SttAdapter, SttCallbacks, SttResult, SttStartOptions } from './types';
export {
  WebSpeechSttAdapter,
  resolveBrowserRecognizerFactory,
  humanizeSpeechError,
} from './web-speech-adapter';
export type {
  SpeechRecognitionFactory,
  SpeechRecognitionLike,
  SpeechRecognitionEventLike,
  SpeechRecognitionResultLike,
  SpeechRecognitionAlternativeLike,
} from './web-speech-adapter';
export { DeepgramSttAdapter, DEEPGRAM_API_KEY_ENV } from './deepgram-adapter';

export type SttEnginePreference = 'auto' | 'web-speech' | 'deepgram';

export interface SttFactoryOptions {
  /** Which engine to build. Default 'auto' (best available). */
  prefer?: SttEnginePreference;
  /** Inject a recognizer factory for the web-speech engine (tests/DI). */
  recognizerFactory?: SpeechRecognitionFactory | null;
  /** Deepgram key for the upgrade seam (read from env at the call site). */
  deepgramApiKey?: string | null;
}

/**
 * Build the speech engine. With 'auto', prefer the high-accuracy Deepgram
 * engine when it is actually usable, otherwise fall back to the browser.
 */
export function createSttAdapter(options: SttFactoryOptions = {}): SttAdapter {
  const prefer = options.prefer ?? 'auto';

  if (prefer === 'web-speech') {
    return new WebSpeechSttAdapter(options.recognizerFactory);
  }
  if (prefer === 'deepgram') {
    return new DeepgramSttAdapter(options.deepgramApiKey);
  }

  // auto: high-accuracy first when usable, else the zero-key browser engine.
  const deepgram = new DeepgramSttAdapter(options.deepgramApiKey);
  if (deepgram.isSupported()) return deepgram;
  return new WebSpeechSttAdapter(options.recognizerFactory);
}

export { DEEPGRAM_API_KEY_ENV as STT_UPGRADE_ENV_KEY };

/**
 * Merge a freshly recognized transcript chunk into an existing draft, joining
 * with a single space and avoiding leading/trailing whitespace. Pure + tested.
 */
export function appendTranscript(existing: string, addition: string): string {
  const add = addition.trim();
  if (!add) return existing;
  const base = existing.replace(/\s+$/, '');
  if (!base) return add;
  return `${base} ${add}`;
}
