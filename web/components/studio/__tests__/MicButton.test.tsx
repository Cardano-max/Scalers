import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { MicButton } from '../MicButton';
import { VoiceTweakPanel } from '../VoiceTweakPanel';
import type {
  SpeechRecognitionLike,
  SpeechRecognitionEventLike,
  SpeechRecognitionResultLike,
} from '@/lib/studio/stt';

/**
 * Wiring tests for the voice mic. A FAKE recognizer is injected through the
 * sttOptions DI seam so no browser is needed. We prove the mic button drives
 * the recognizer and that a FINAL transcript flows into the existing chat draft
 * and out through the existing onSend path — i.e. voice feeds the SAME text
 * backend, nothing else changes.
 */

class FakeRecognition implements SpeechRecognitionLike {
  lang = '';
  continuous = false;
  interimResults = false;
  started = false;
  onresult: ((e: SpeechRecognitionEventLike) => void) | null = null;
  onerror: ((e: { error?: string }) => void) | null = null;
  onend: (() => void) | null = null;
  start() {
    this.started = true;
  }
  stop() {
    this.onend?.();
  }
  emit(transcript: string, isFinal: boolean) {
    const result = { 0: { transcript }, length: 1, isFinal } as unknown as SpeechRecognitionResultLike;
    this.onresult?.({ resultIndex: 0, results: [result] });
  }
}

/** A recognizer factory that records every instance it builds. */
function captureFactory() {
  const instances: FakeRecognition[] = [];
  const factory = () => {
    const rec = new FakeRecognition();
    instances.push(rec);
    return rec;
  };
  return { factory, instances };
}

describe('MicButton', () => {
  it('renders an enabled mic when the engine is supported', () => {
    const { factory } = captureFactory();
    render(<MicButton onTranscript={vi.fn()} sttOptions={{ recognizerFactory: factory }} />);
    expect(screen.getByRole('button', { name: 'Start voice input' })).toBeEnabled();
  });

  it('renders a disabled mic and honest title when unsupported', () => {
    render(<MicButton onTranscript={vi.fn()} sttOptions={{ recognizerFactory: null }} />);
    const btn = screen.getByRole('button', { name: /not supported/i });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute('title', expect.stringContaining('not supported'));
  });

  it('starts the recognizer on click and forwards the FINAL transcript', () => {
    const { factory, instances } = captureFactory();
    const onTranscript = vi.fn();
    render(<MicButton onTranscript={onTranscript} sttOptions={{ recognizerFactory: factory }} />);

    fireEvent.click(screen.getByRole('button', { name: 'Start voice input' }));
    expect(instances).toHaveLength(1);
    expect(instances[0].started).toBe(true);

    // Interim chunk does NOT commit; final chunk does.
    act(() => instances[0].emit('fill may', false));
    expect(onTranscript).not.toHaveBeenCalled();
    act(() => instances[0].emit('fill may tuesdays', true));
    expect(onTranscript).toHaveBeenCalledWith('fill may tuesdays');
  });
});

describe('VoiceTweakPanel — voice feeds the existing text composer', () => {
  it('drops a dictated transcript into the draft and sends it via onSend', () => {
    const { factory, instances } = captureFactory();
    const onSend = vi.fn();
    render(
      <VoiceTweakPanel
        turns={[]}
        onSend={onSend}
        streamStatus="open"
        sessionId="s1"
        micOptions={{ recognizerFactory: factory }}
      />,
    );

    // Dictate into the existing text input.
    fireEvent.click(screen.getByRole('button', { name: 'Start voice input' }));
    act(() => instances[0].emit('plan a may promo', true));

    const textarea = screen.getByLabelText('Tweak the plan or type a brief') as HTMLTextAreaElement;
    expect(textarea.value).toBe('plan a may promo');

    // The SAME existing send path handles it — no new backend.
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(onSend).toHaveBeenCalledWith('plan a may promo');
  });

  it('appends a dictated chunk after typed text with a single space', () => {
    const { factory, instances } = captureFactory();
    render(
      <VoiceTweakPanel
        turns={[]}
        onSend={vi.fn()}
        streamStatus="open"
        sessionId="s1"
        micOptions={{ recognizerFactory: factory }}
      />,
    );
    const textarea = screen.getByLabelText('Tweak the plan or type a brief') as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'Goal:' } });

    fireEvent.click(screen.getByRole('button', { name: 'Start voice input' }));
    act(() => instances[0].emit('fill empty Tuesdays', true));

    expect(textarea.value).toBe('Goal: fill empty Tuesdays');
  });
});
