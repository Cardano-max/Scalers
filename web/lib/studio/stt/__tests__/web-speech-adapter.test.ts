import { describe, it, expect, vi } from 'vitest';
import {
  WebSpeechSttAdapter,
  humanizeSpeechError,
  type SpeechRecognitionLike,
  type SpeechRecognitionEventLike,
  type SpeechRecognitionResultLike,
} from '../web-speech-adapter';
import type { SttResult } from '../types';

/**
 * Hermetic unit tests for the Web Speech STT adapter. No browser, no real
 * SpeechRecognition: we drive a FAKE recognizer through the same SpeechRecognitionLike
 * surface the adapter consumes, and assert the adapter forwards exactly what the
 * recognizer emits (interim + final), maps errors, and cleans up on stop/end.
 */

/** A controllable fake recognizer implementing the minimal SpeechRecognition surface. */
class FakeRecognition implements SpeechRecognitionLike {
  lang = '';
  continuous = false;
  interimResults = false;
  started = false;
  stopped = false;
  onresult: ((e: SpeechRecognitionEventLike) => void) | null = null;
  onerror: ((e: { error?: string; message?: string }) => void) | null = null;
  onend: (() => void) | null = null;

  start() {
    this.started = true;
  }
  stop() {
    this.stopped = true;
    // Real browsers fire `end` after stop(); emulate it.
    this.onend?.();
  }

  /** Test helper: emit a recognition result frame. */
  emit(transcript: string, isFinal: boolean, resultIndex = 0) {
    const result = {
      0: { transcript },
      length: 1,
      isFinal,
    } as unknown as SpeechRecognitionResultLike;
    const event: SpeechRecognitionEventLike = {
      resultIndex,
      results: [result],
    };
    this.onresult?.(event);
  }

  /** Test helper: emit a terminal error. */
  fail(code: string) {
    this.onerror?.({ error: code });
  }
}

function makeAdapter() {
  const rec = new FakeRecognition();
  const adapter = new WebSpeechSttAdapter(() => rec);
  return { rec, adapter };
}

describe('WebSpeechSttAdapter', () => {
  it('is supported when a recognizer factory is available, unsupported when null', () => {
    expect(new WebSpeechSttAdapter(() => new FakeRecognition()).isSupported()).toBe(true);
    expect(new WebSpeechSttAdapter(null).isSupported()).toBe(false);
  });

  it('exposes a stable id + human label for honest badging', () => {
    const { adapter } = makeAdapter();
    expect(adapter.id).toBe('web-speech');
    expect(adapter.label).toBe('Browser (Web Speech)');
  });

  it('applies start options and begins capture', () => {
    const { rec, adapter } = makeAdapter();
    adapter.start({}, { lang: 'en-GB', interimResults: true, continuous: true });
    expect(rec.started).toBe(true);
    expect(rec.lang).toBe('en-GB');
    expect(rec.interimResults).toBe(true);
    expect(rec.continuous).toBe(true);
  });

  it('forwards interim then final transcript chunks to onResult', () => {
    const { rec, adapter } = makeAdapter();
    const results: SttResult[] = [];
    adapter.start({ onResult: (r) => results.push(r) });
    rec.emit('fill may', false);
    rec.emit('fill may tuesdays', true);
    expect(results).toEqual([
      { transcript: 'fill may', isFinal: false },
      { transcript: 'fill may tuesdays', isFinal: true },
    ]);
  });

  it('maps a raw error code to friendly copy via onError', () => {
    const { rec, adapter } = makeAdapter();
    const onError = vi.fn();
    adapter.start({ onError });
    rec.fail('not-allowed');
    expect(onError).toHaveBeenCalledWith(
      'Microphone permission was blocked. Allow mic access and try again.',
    );
  });

  it('reports onError (not throw) when unsupported', () => {
    const adapter = new WebSpeechSttAdapter(null);
    const onError = vi.fn();
    expect(() => adapter.start({ onError })).not.toThrow();
    expect(onError).toHaveBeenCalledWith('Speech recognition is not supported in this browser.');
  });

  it('stop() halts the recognizer and fires onEnd', () => {
    const { rec, adapter } = makeAdapter();
    const onEnd = vi.fn();
    adapter.start({ onEnd });
    adapter.stop();
    expect(rec.stopped).toBe(true);
    expect(onEnd).toHaveBeenCalledTimes(1);
  });

  it('stop() is a safe no-op when idle', () => {
    const { adapter } = makeAdapter();
    expect(() => adapter.stop()).not.toThrow();
  });

  it('handles a recognizer that throws on start() without crashing', () => {
    const rec = new FakeRecognition();
    rec.start = () => {
      throw new Error('already started');
    };
    const adapter = new WebSpeechSttAdapter(() => rec);
    const onError = vi.fn();
    adapter.start({ onError });
    expect(onError).toHaveBeenCalledWith('already started');
  });
});

describe('humanizeSpeechError', () => {
  it('maps known codes', () => {
    expect(humanizeSpeechError('no-speech')).toMatch(/No speech detected/);
    expect(humanizeSpeechError('audio-capture')).toMatch(/No microphone/);
    expect(humanizeSpeechError('network')).toMatch(/Network error/);
  });
  it('falls back for unknown / missing codes', () => {
    expect(humanizeSpeechError('weird')).toMatch(/weird/);
    expect(humanizeSpeechError(undefined)).toMatch(/Speech recognition failed/);
  });
});
