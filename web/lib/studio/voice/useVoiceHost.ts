'use client';

/**
 * useVoiceHost — the speech-to-speech voice session, lifted out of VoiceConsole so a
 * richer hero (the audio-reactive orb) can bind to its real state without changing
 * any session logic. It is the SAME RealtimeVoiceSession: ephemeral secret, WebRTC
 * gpt-realtime, exactly two server-side tools (update_plan + request_orchestration),
 * the SERVER-SIDE 2-factor spoken-GO gate. It is SEND-INCAPABLE — the only publish
 * path remains the Review-Queue approve.
 *
 * Differences from the old inline console, all additive:
 *  - exposes the REAL mic + model-TTS MediaStreams (for the orb's AnalyserNodes),
 *  - exposes `hostSpeaking` (the host is currently talking — orb pulses host-accent),
 *  - on a valid GO it hands the launched runId to the caller (`onRunLaunched`) so the
 *    SHARED studio run drives the reasoning stream, instead of polling here,
 *  - narrates each newly-landed agent from the shared `runState` the caller passes in.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { RealtimeVoiceSession, type VoiceConnState } from './session';
import type { PlanUpdateResult, OrchestrateResult } from './realtime';
import type { RunState, RunStep } from '../run-trace';

function stepLabel(role: string): string {
  switch ((role || '').toLowerCase()) {
    case 'researcher':
      return 'Researcher';
    case 'strategist':
      return 'Strategist';
    case 'draft':
      return 'Copywriter';
    case 'critic':
      return 'Critic';
    case 'jury':
      return 'Jury';
    default:
      return role || 'Studio';
  }
}

function stepSummary(step: RunStep): string {
  const o = step.output;
  if (o && typeof o === 'object') {
    try {
      return JSON.stringify(o).slice(0, 180);
    } catch {
      return '';
    }
  }
  return o == null ? '' : String(o).slice(0, 180);
}

export interface UseVoiceHost {
  conn: VoiceConnState;
  live: boolean;
  busy: boolean;
  userLine: string;
  assistantLine: string;
  readback: string;
  awaitingGo: boolean;
  hostSpeaking: boolean;
  micStream: MediaStream | null;
  remoteStream: MediaStream | null;
  error: string | null;
  start: () => void;
  stop: () => void;
}

export function useVoiceHost(
  aguiUrl: string,
  sessionId: string,
  /** The SHARED run state (drives narration of each landing agent). */
  runState: RunState | null,
  opts: {
    disabled?: boolean;
    /** A valid GO launched the held spine — hand the runId to the shared studio. */
    onRunLaunched?: (runId: string) => void;
    /** Reflect the server-persisted voice plan edit into the shared plan panel. */
    onPlan?: (plan: Record<string, unknown>) => void;
    /** A finalized OPERATOR utterance — record it into the unified transcript so
     *  spoken and typed turns are ONE conversation. */
    onUserFinal?: (text: string) => void;
    /** A finalized HOST utterance — record it into the unified transcript. */
    onAssistantFinal?: (text: string) => void;
  } = {},
): UseVoiceHost {
  const { disabled = false, onRunLaunched, onPlan, onUserFinal, onAssistantFinal } = opts;

  const [conn, setConn] = useState<VoiceConnState>('idle');
  const [userLine, setUserLine] = useState('');
  const [assistantLine, setAssistantLine] = useState('');
  const [readback, setReadback] = useState('');
  const [awaitingGo, setAwaitingGo] = useState(false);
  const [hostSpeaking, setHostSpeaking] = useState(false);
  const [micStream, setMicStream] = useState<MediaStream | null>(null);
  const [remoteStream, setRemoteStream] = useState<MediaStream | null>(null);
  const [error, setError] = useState<string | null>(null);

  const sessionRef = useRef<RealtimeVoiceSession | null>(null);
  const narratedRef = useRef<Set<number>>(new Set());
  const speakingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Keep the latest callbacks/runState in refs so the session closure stays stable.
  const onLaunchedRef = useRef(onRunLaunched);
  onLaunchedRef.current = onRunLaunched;
  const onPlanRef = useRef(onPlan);
  onPlanRef.current = onPlan;
  const onUserFinalRef = useRef(onUserFinal);
  onUserFinalRef.current = onUserFinal;
  const onAssistantFinalRef = useRef(onAssistantFinal);
  onAssistantFinalRef.current = onAssistantFinal;

  // Narrate each NEW landed agent through the realtime voice as it appears in the
  // shared run state. Honest: only fires for real steps, once each.
  useEffect(() => {
    if (!runState) {
      narratedRef.current = new Set();
      return;
    }
    for (const s of runState.steps) {
      if (!narratedRef.current.has(s.seq)) {
        narratedRef.current.add(s.seq);
        sessionRef.current?.narrate(
          `The ${stepLabel(s.role)} just finished. In one short sentence, tell the operator: ${stepSummary(s)}`,
        );
      }
    }
    if (runState.status === 'completed') {
      sessionRef.current?.narrate(
        'Tell the operator the run is complete: every draft is HELD for approval and nothing was sent.',
      );
    }
  }, [runState]);

  const handlePlan = useCallback((r: PlanUpdateResult) => {
    if (r.readback) setReadback(r.readback);
    setAwaitingGo(!!r.awaitingGo);
    if (r.plan) onPlanRef.current?.(r.plan);
  }, []);

  const handleOrchestrate = useCallback((r: OrchestrateResult) => {
    if (r.launched && r.runId) {
      setError(null);
      narratedRef.current = new Set();
      onLaunchedRef.current?.(r.runId);
    }
  }, []);

  const start = useCallback(() => {
    if (disabled || conn === 'live' || conn === 'connecting' || conn === 'minting') return;
    setError(null);
    const s = new RealtimeVoiceSession(aguiUrl, sessionId, {
      onStatus: setConn,
      onUserTranscript: (t) => {
        setUserLine(t);
        // A finalized spoken line — record it into the ONE transcript (same session).
        onUserFinalRef.current?.(t);
      },
      onAssistantTranscript: (t, done) => {
        setAssistantLine((prev) => (done ? t : prev + t));
        // Host is speaking while deltas arrive; clear shortly after the final.
        setHostSpeaking(true);
        if (speakingTimer.current) clearTimeout(speakingTimer.current);
        speakingTimer.current = setTimeout(() => setHostSpeaking(false), done ? 350 : 1200);
        // On the final, record the host's spoken line into the unified transcript.
        if (done) onAssistantFinalRef.current?.(t);
      },
      onPlan: handlePlan,
      onOrchestrate: handleOrchestrate,
      onError: setError,
      onMicStream: setMicStream,
      onRemoteStream: setRemoteStream,
    });
    sessionRef.current = s;
    void s.connect();
  }, [aguiUrl, sessionId, disabled, conn, handlePlan, handleOrchestrate]);

  const stop = useCallback(() => {
    sessionRef.current?.close();
    sessionRef.current = null;
    if (speakingTimer.current) clearTimeout(speakingTimer.current);
    setHostSpeaking(false);
    setMicStream(null);
    setRemoteStream(null);
  }, []);

  useEffect(
    () => () => {
      sessionRef.current?.close();
      if (speakingTimer.current) clearTimeout(speakingTimer.current);
    },
    [],
  );

  const live = conn === 'live';
  const busy = conn === 'minting' || conn === 'connecting';

  return {
    conn,
    live,
    busy,
    userLine,
    assistantLine,
    readback,
    awaitingGo,
    hostSpeaking,
    micStream,
    remoteStream,
    error,
    start,
    stop,
  };
}
