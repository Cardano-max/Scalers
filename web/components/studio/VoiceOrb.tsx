'use client';

/**
 * VoiceOrb — the focal point of the Voice hero. A canvas orb that is HONEST by
 * construction: when the session is live it is driven by a real AnalyserNode on
 * the actual WebRTC mic stream (your voice) and the model-TTS stream (the host's
 * voice). Amplitude → scale + organic displacement; it only moves because real
 * audio is moving it.
 *
 * State map (bound to the real VoiceConnState + the run/awaitingGo flags):
 *   idle            → dim, slow breathing (CSS), no audio
 *   minting/connecting (busy) → indeterminate shimmer ring
 *   live, !awaitingGo → "interviewing": reacts to your mic; pulses host-accent
 *                       (#6D4AE6) while the host speaks
 *   live,  awaitingGo → "ready to run": teal ready wash
 *
 * Respects prefers-reduced-motion: no rAF loop, a static state-colored orb.
 */
import { useEffect, useRef } from 'react';
import type { VoiceConnState } from '@/lib/studio/voice/session';

const HOST_ACCENT = '#6D4AE6';
const TEAL = '#0F8A82';
const IDLE = '#A8A299';

function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

export interface VoiceOrbProps {
  connState: VoiceConnState;
  awaitingGo: boolean;
  hostSpeaking: boolean;
  micStream: MediaStream | null;
  remoteStream: MediaStream | null;
  /** Rendered diameter in CSS px. */
  size?: number;
  onClick?: () => void;
  disabled?: boolean;
  ariaLabel?: string;
}

export function VoiceOrb({
  connState,
  awaitingGo,
  hostSpeaking,
  micStream,
  remoteStream,
  size = 240,
  onClick,
  disabled = false,
  ariaLabel = 'Voice host orb',
}: VoiceOrbProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const rafRef = useRef<number | null>(null);

  // Keep the latest state in refs so the long-lived rAF closure reads fresh values
  // without being torn down on every prop change.
  const stateRef = useRef({ connState, awaitingGo, hostSpeaking });
  stateRef.current = { connState, awaitingGo, hostSpeaking };

  // ── Audio analysers (real WebRTC streams) ────────────────────────────────
  const ctxRef = useRef<AudioContext | null>(null);
  const micAnalyserRef = useRef<AnalyserNode | null>(null);
  const remoteAnalyserRef = useRef<AnalyserNode | null>(null);

  const attach = (stream: MediaStream | null, which: 'mic' | 'remote') => {
    if (!stream || typeof window === 'undefined') return;
    const AC =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AC) return;
    if (!ctxRef.current) {
      try {
        ctxRef.current = new AC();
      } catch {
        return;
      }
    }
    const ctx = ctxRef.current;
    try {
      const src = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.75;
      src.connect(analyser);
      if (which === 'mic') micAnalyserRef.current = analyser;
      else remoteAnalyserRef.current = analyser;
    } catch {
      /* analyser attach is best-effort; the orb falls back to its state color */
    }
  };

  useEffect(() => {
    attach(micStream, 'mic');
  }, [micStream]);
  useEffect(() => {
    attach(remoteStream, 'remote');
  }, [remoteStream]);

  useEffect(
    () => () => {
      micAnalyserRef.current = null;
      remoteAnalyserRef.current = null;
      const ctx = ctxRef.current;
      ctxRef.current = null;
      if (ctx) void ctx.close().catch(() => {});
    },
    [],
  );

  // ── Draw loop ─────────────────────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = typeof window !== 'undefined' ? Math.min(window.devicePixelRatio || 1, 2) : 1;
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    const g = canvas.getContext('2d');
    if (!g) return;
    g.scale(dpr, dpr);

    const reduced = prefersReducedMotion();
    const cx = size / 2;
    const cy = size / 2;
    const baseR = size * 0.3;

    const levelOf = (a: AnalyserNode | null): number => {
      if (!a) return 0;
      const buf = new Uint8Array(a.frequencyBinCount);
      a.getByteFrequencyData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i];
      return sum / buf.length / 255; // 0..1
    };

    let smoothed = 0;

    const colorFor = (): [number, number, number] => {
      const s = stateRef.current;
      if (s.connState === 'idle' || s.connState === 'closed' || s.connState === 'error')
        return hexToRgb(IDLE);
      if (s.connState === 'minting' || s.connState === 'connecting') return hexToRgb(IDLE);
      if (s.awaitingGo) return hexToRgb(TEAL);
      if (s.hostSpeaking) return hexToRgb(HOST_ACCENT);
      return hexToRgb(HOST_ACCENT);
    };

    const draw = (t: number) => {
      const s = stateRef.current;
      const live = s.connState === 'live';
      const micL = live ? levelOf(micAnalyserRef.current) : 0;
      const remL = live ? levelOf(remoteAnalyserRef.current) : 0;
      const raw = Math.max(micL, remL * 1.1);
      smoothed += (raw - smoothed) * 0.25;

      const [r, gg, b] = colorFor();
      const idlePulse = reduced ? 0 : (Math.sin(t / 1100) + 1) / 2; // 0..1 ambient
      const amp = live ? smoothed : idlePulse * 0.12;
      const radius = baseR * (1 + amp * 0.42);

      g.clearRect(0, 0, size, size);

      // Soft outer glow proportional to amplitude.
      const glow = g.createRadialGradient(cx, cy, radius * 0.5, cx, cy, radius * 2.1);
      glow.addColorStop(0, `rgba(${r},${gg},${b},${0.22 + amp * 0.3})`);
      glow.addColorStop(1, `rgba(${r},${gg},${b},0)`);
      g.fillStyle = glow;
      g.beginPath();
      g.arc(cx, cy, radius * 2.1, 0, Math.PI * 2);
      g.fill();

      // Organic blob: many points with cheap multi-sine ("pseudo-perlin") wobble,
      // displacement scaled by real amplitude.
      const points = 64;
      g.beginPath();
      for (let i = 0; i <= points; i++) {
        const ang = (i / points) * Math.PI * 2;
        const wob = reduced
          ? 0
          : Math.sin(ang * 3 + t / 380) * 0.5 +
            Math.sin(ang * 5 - t / 540) * 0.3 +
            Math.sin(ang * 2 + t / 900) * 0.2;
        const rr = radius * (1 + wob * (0.04 + amp * 0.16));
        const x = cx + Math.cos(ang) * rr;
        const y = cy + Math.sin(ang) * rr;
        if (i === 0) g.moveTo(x, y);
        else g.lineTo(x, y);
      }
      g.closePath();
      const fill = g.createRadialGradient(
        cx - radius * 0.3,
        cy - radius * 0.3,
        radius * 0.1,
        cx,
        cy,
        radius * 1.15,
      );
      fill.addColorStop(0, `rgba(${Math.min(r + 40, 255)},${Math.min(gg + 40, 255)},${Math.min(b + 40, 255)},0.96)`);
      fill.addColorStop(1, `rgba(${r},${gg},${b},0.9)`);
      g.fillStyle = fill;
      g.fill();

      // Inner highlight.
      g.beginPath();
      g.arc(cx - radius * 0.22, cy - radius * 0.24, radius * 0.34, 0, Math.PI * 2);
      g.fillStyle = 'rgba(255,255,255,0.18)';
      g.fill();

      if (!reduced) rafRef.current = requestAnimationFrame(draw);
    };

    if (reduced) {
      draw(0);
    } else {
      rafRef.current = requestAnimationFrame(draw);
    }

    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [size]);

  const busy = connState === 'minting' || connState === 'connecting';

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={ariaLabel}
      data-conn={connState}
      data-awaiting-go={awaitingGo ? 'true' : 'false'}
      style={{
        position: 'relative',
        width: size,
        height: size,
        borderRadius: '50%',
        border: 'none',
        background: 'transparent',
        padding: 0,
        cursor: disabled ? 'not-allowed' : 'pointer',
        display: 'grid',
        placeItems: 'center',
        WebkitTapHighlightColor: 'transparent',
      }}
    >
      {/* Indeterminate ring for the busy (minting/connecting) state. */}
      {busy && (
        <span
          aria-hidden
          className="ring-spin"
          style={{
            position: 'absolute',
            inset: size * 0.16,
            borderRadius: '50%',
            border: `2px solid ${IDLE}33`,
            borderTopColor: IDLE,
          }}
        />
      )}
      <canvas
        ref={canvasRef}
        className={connState === 'idle' ? 'orb-breathe' : undefined}
        style={{ width: size, height: size, display: 'block' }}
      />
    </button>
  );
}
