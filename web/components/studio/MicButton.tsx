'use client';

/**
 * MicButton — voice-to-text control for the Campaign Studio composer.
 *
 * Click to start dictation; the recognized FINAL transcript is handed to
 * `onTranscript` (the composer appends it to the existing text input — nothing
 * else changes, the same text backend handles it). While listening it shows a
 * live interim caption and a BADGE naming the engine (honest: "Browser" today;
 * the high-accuracy Deepgram engine swaps in behind the same seam).
 *
 * Degrades honestly: in a browser without the Web Speech API the button is
 * rendered disabled with an explanatory title — never hidden, never faked.
 */
import { useEffect, useState } from 'react';
import { useStt } from '@/lib/studio/stt/useStt';
import type { SttFactoryOptions } from '@/lib/studio/stt';

interface MicButtonProps {
  /** Receives each FINAL transcript chunk (trimmed, non-empty). */
  onTranscript: (text: string) => void;
  /** Disable while the composer is busy. */
  disabled?: boolean;
  /** STT factory options (engine preference / DI recognizer for tests). */
  sttOptions?: SttFactoryOptions;
}

function MicIcon({ active }: { active: boolean }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke={active ? '#fff' : '#0F8A82'}
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="9" y="2" width="6" height="11" rx="3" />
      <path d="M5 10a7 7 0 0 0 14 0" />
      <line x1="12" y1="19" x2="12" y2="22" />
    </svg>
  );
}

export function MicButton({ onTranscript, disabled = false, sttOptions }: MicButtonProps) {
  const stt = useStt(onTranscript, sttOptions);

  // Mic support depends on browser-only globals (SpeechRecognition), so it differs
  // between the server render (always unsupported) and the client. Gate the live UI
  // behind a post-mount flag so SSR and the FIRST client render are identical (the
  // disabled placeholder), then swap to the real control after hydration. Without
  // this the button flips on hydration and React throws a mismatch warning.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  if (!mounted || !stt.supported) {
    return (
      <button
        type="button"
        disabled
        aria-label="Voice input not supported in this browser"
        title="Voice input is not supported in this browser. Type your message instead."
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 40,
          height: 40,
          border: '1px solid var(--hairline)',
          borderRadius: 9,
          background: '#F5F3EF',
          color: '#A8A299',
          cursor: 'not-allowed',
          opacity: 0.55,
        }}
      >
        <MicIcon active={false} />
      </button>
    );
  }

  const { listening, interim, error, adapterLabel, toggle } = stt;

  return (
    <div style={{ position: 'relative', display: 'inline-flex' }}>
      <button
        type="button"
        onClick={toggle}
        disabled={disabled}
        aria-label={listening ? 'Stop voice input' : 'Start voice input'}
        aria-pressed={listening}
        title={
          listening
            ? `Listening via ${adapterLabel} — click to stop`
            : `Start voice input (${adapterLabel})`
        }
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 40,
          height: 40,
          border: listening ? '1px solid #0F8A82' : '1px solid var(--hairline)',
          borderRadius: 9,
          background: listening ? '#0F8A82' : '#fff',
          cursor: disabled ? 'not-allowed' : 'pointer',
          opacity: disabled ? 0.5 : 1,
          transition: 'background 120ms ease',
        }}
      >
        <MicIcon active={listening} />
      </button>

      {/* Live caption + honest engine badge while listening. */}
      {listening && (
        <div
          role="status"
          aria-live="polite"
          style={{
            position: 'absolute',
            bottom: 'calc(100% + 8px)',
            right: 0,
            minWidth: 200,
            maxWidth: 320,
            padding: '8px 10px',
            background: '#0F1A19',
            color: '#E8F3F1',
            borderRadius: 9,
            fontSize: 12,
            lineHeight: 1.4,
            boxShadow: '0 6px 20px rgba(0,0,0,0.18)',
            zIndex: 5,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: interim ? 4 : 0 }}>
            <span
              aria-hidden="true"
              style={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                background: '#34D399',
                animation: 'micPulse 1s ease-in-out infinite',
              }}
            />
            <span style={{ fontWeight: 600 }}>Listening</span>
            <span style={{ marginLeft: 'auto', fontSize: 10, color: '#7FB7B0' }}>{adapterLabel}</span>
          </div>
          {interim && <div style={{ color: '#B7CFCB', fontStyle: 'italic' }}>{interim}</div>}
        </div>
      )}

      {/* Honest error surface (e.g. mic permission blocked, no speech). */}
      {error && !listening && (
        <div
          role="alert"
          style={{
            position: 'absolute',
            bottom: 'calc(100% + 8px)',
            right: 0,
            minWidth: 200,
            maxWidth: 320,
            padding: '8px 10px',
            background: '#FEF2F2',
            border: '1px solid #FBD5D5',
            color: '#B42318',
            borderRadius: 9,
            fontSize: 12,
            lineHeight: 1.4,
            zIndex: 5,
          }}
        >
          {error}
        </div>
      )}

      <style>{`
        @keyframes micPulse { 0%, 100% { transform: scale(1); opacity: 1; } 50% { transform: scale(1.5); opacity: 0.5; } }
      `}</style>
    </div>
  );
}
