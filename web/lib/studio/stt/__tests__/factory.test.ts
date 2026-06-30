import { describe, it, expect, vi } from 'vitest';
import {
  createSttAdapter,
  appendTranscript,
  WebSpeechSttAdapter,
  DeepgramSttAdapter,
  DEEPGRAM_API_KEY_ENV,
} from '../index';
import type { SpeechRecognitionLike } from '../web-speech-adapter';

/**
 * Tests for the STT factory seam + helpers. Proves: 'auto' picks the zero-key
 * browser engine while Deepgram is an honest stub; explicit preferences build
 * the right engine; the Deepgram seam names the EXACT key; transcript merge is
 * whitespace-correct.
 */

function fakeFactory() {
  return () =>
    ({
      lang: '',
      continuous: false,
      interimResults: false,
      start() {},
      stop() {},
      onresult: null,
      onerror: null,
      onend: null,
    }) as SpeechRecognitionLike;
}

describe('createSttAdapter', () => {
  it("'auto' falls back to the browser engine (Deepgram stub is unsupported today)", () => {
    const adapter = createSttAdapter({ recognizerFactory: fakeFactory(), deepgramApiKey: 'present' });
    expect(adapter).toBeInstanceOf(WebSpeechSttAdapter);
    expect(adapter.id).toBe('web-speech');
  });

  it("'web-speech' builds the browser engine explicitly", () => {
    const adapter = createSttAdapter({ prefer: 'web-speech', recognizerFactory: fakeFactory() });
    expect(adapter).toBeInstanceOf(WebSpeechSttAdapter);
    expect(adapter.isSupported()).toBe(true);
  });

  it("'deepgram' builds the Deepgram seam (honest-degrade stub)", () => {
    const adapter = createSttAdapter({ prefer: 'deepgram', deepgramApiKey: null });
    expect(adapter).toBeInstanceOf(DeepgramSttAdapter);
    expect(adapter.id).toBe('deepgram');
    expect(adapter.isSupported()).toBe(false);
  });
});

describe('DeepgramSttAdapter (upgrade seam)', () => {
  it('names the exact key when no key is configured', () => {
    const adapter = new DeepgramSttAdapter(null);
    const onError = vi.fn();
    adapter.start({ onError });
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toContain(DEEPGRAM_API_KEY_ENV);
    expect(DEEPGRAM_API_KEY_ENV).toBe('DEEPGRAM_API_KEY');
  });

  it('is honest that it is not implemented even when a key is present', () => {
    const adapter = new DeepgramSttAdapter('dg_live_xxx');
    expect(adapter.isSupported()).toBe(false);
    const onError = vi.fn();
    adapter.start({ onError });
    expect(onError.mock.calls[0][0]).toMatch(/not implemented/i);
  });
});

describe('appendTranscript', () => {
  it('returns the addition when the draft is empty', () => {
    expect(appendTranscript('', 'hello world')).toBe('hello world');
  });
  it('joins with a single space, trimming both sides', () => {
    expect(appendTranscript('Plan a campaign', 'for May')).toBe('Plan a campaign for May');
    expect(appendTranscript('Plan a campaign ', '  for May  ')).toBe('Plan a campaign for May');
  });
  it('ignores an empty/whitespace addition', () => {
    expect(appendTranscript('keep me', '   ')).toBe('keep me');
  });
});
