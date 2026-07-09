import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { VoiceOrb } from '../VoiceOrb';

/**
 * The orb is HONEST by construction: it only moves because real audio moves it. With
 * no audio device / no streams it must still render and stay interactive (the type-
 * first fallback path) and never throw — jsdom has no canvas 2d context, so this also
 * pins the graceful no-context fallback.
 */
describe('VoiceOrb — graceful fallback without audio', () => {
  it('renders an interactive button with no mic/remote stream and does not crash', () => {
    const onClick = vi.fn();
    render(
      <VoiceOrb
        connState="idle"
        awaitingGo={false}
        hostSpeaking={false}
        micStream={null}
        remoteStream={null}
        onClick={onClick}
        ariaLabel="Start voice session"
      />,
    );
    const btn = screen.getByRole('button', { name: 'Start voice session' });
    expect(btn).toHaveAttribute('data-conn', 'idle');
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('reflects the awaiting-GO state via a data attribute (real state, not decoration)', () => {
    render(
      <VoiceOrb
        connState="live"
        awaitingGo
        hostSpeaking={false}
        micStream={null}
        remoteStream={null}
      />,
    );
    expect(screen.getByRole('button')).toHaveAttribute('data-awaiting-go', 'true');
  });

  it('is disabled (no-op) when the session cannot start', () => {
    const onClick = vi.fn();
    render(
      <VoiceOrb
        connState="idle"
        awaitingGo={false}
        hostSpeaking={false}
        micStream={null}
        remoteStream={null}
        disabled
        onClick={onClick}
      />,
    );
    fireEvent.click(screen.getByRole('button'));
    expect(onClick).not.toHaveBeenCalled();
  });
});
