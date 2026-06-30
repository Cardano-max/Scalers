'use client';

/**
 * VoiceConsole — the speech-to-speech voice host for the Campaign Studio.
 *
 * Click "Start voice" → the browser mints an EPHEMERAL Realtime secret from our
 * server (the raw OPENAI_API_KEY never leaves the backend), opens a WebRTC mic+voice
 * session with the gpt-realtime model, and runs the scoping interview. The model has
 * EXACTLY two tools (update_plan + request_orchestration); both are handled on the
 * SERVER. update_plan persists the plan (shown here as a live readback); a valid
 * spoken GO passes the SERVER-SIDE 2-factor gate, launches the EXISTING held
 * /studio/run spine, and we render the SAME OrchestrationFlow + narrate each agent's
 * result. NOTHING is sent — every output stays HELD behind the Review-Queue approve.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { ChatTurn } from '@/lib/data/studio-adapter';
import { OrchestrationFlow } from './OrchestrationFlow';
import { fetchRunState, type RunState, type RunStep } from '@/lib/studio/run-trace';
import { RealtimeVoiceSession, type VoiceConnState } from '@/lib/studio/voice/session';
import type { PlanUpdateResult, OrchestrateResult } from '@/lib/studio/voice/realtime';

interface VoiceConsoleProps {
  aguiUrl: string;
  sessionId: string;
  /** Disabled when the studio backend is unreachable (honest not-connected state). */
  disabled?: boolean;
  /** Reflect a server-persisted plan update into the parent's shared plan panel. */
  onPlan?: (plan: Record<string, unknown>) => void;
}

function stepRole(role: string): { role: ChatTurn['role']; label: string } {
  switch ((role || '').toLowerCase()) {
    case 'researcher':
      return { role: 'RESEARCHER', label: 'Researcher' };
    case 'strategist':
      return { role: 'STRATEGIST', label: 'Strategist' };
    case 'draft':
      return { role: 'COPYWRITER', label: 'Draft' };
    case 'critic':
      return { role: 'CRITIC', label: 'Critic' };
    case 'jury':
      return { role: 'JURY', label: 'Jury' };
    default:
      return { role: 'SYSTEM', label: role || 'Studio' };
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

/** Map run steps to the ChatTurn[] OrchestrationFlow derives its stages from. */
function runTurns(runState: RunState | null): ChatTurn[] {
  if (!runState) return [];
  return runState.steps.map((s) => {
    const p = stepRole(s.role);
    return {
      id: `voice_runstep_${runState.runId || 'live'}_${s.seq}`,
      role: p.role,
      label: p.label,
      text: stepSummary(s) || `${p.label} working…`,
      at: s.createdAt ?? new Date().toISOString(),
    };
  });
}

export function VoiceConsole({ aguiUrl, sessionId, disabled = false, onPlan }: VoiceConsoleProps) {
  const [conn, setConn] = useState<VoiceConnState>('idle');
  const [userLine, setUserLine] = useState('');
  const [assistantLine, setAssistantLine] = useState('');
  const [readback, setReadback] = useState('');
  const [awaitingGo, setAwaitingGo] = useState(false);
  const [runState, setRunState] = useState<RunState | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sessionRef = useRef<RealtimeVoiceSession | null>(null);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const narratedRef = useRef<Set<number>>(new Set());

  const startRunPolling = useCallback(
    (runId: string) => {
      setRunning(true);
      narratedRef.current = new Set();
      let attempts = 0;
      const poll = async () => {
        attempts += 1;
        try {
          const st = await fetchRunState(aguiUrl, runId);
          setRunState(st);
          // Narrate each NEW agent result through the realtime voice as it lands.
          for (const s of st.steps) {
            if (!narratedRef.current.has(s.seq)) {
              narratedRef.current.add(s.seq);
              const { label } = stepRole(s.role);
              sessionRef.current?.narrate(
                `The ${label} just finished. In one short sentence, tell the operator: ${stepSummary(s)}`,
              );
            }
          }
          if (st.status === 'completed' || st.status === 'error') {
            setRunning(false);
            if (st.status === 'error' && st.error) setError(st.error);
            else
              sessionRef.current?.narrate(
                'Tell the operator the run is complete: every draft is HELD for approval and nothing was sent.',
              );
            return;
          }
        } catch {
          /* transient — keep polling */
        }
        if (attempts >= 80) {
          setRunning(false);
          return;
        }
        pollRef.current = setTimeout(poll, 1500);
      };
      pollRef.current = setTimeout(poll, 1200);
    },
    [aguiUrl],
  );

  const handlePlan = useCallback(
    (r: PlanUpdateResult) => {
      if (r.readback) setReadback(r.readback);
      setAwaitingGo(!!r.awaitingGo);
      if (r.plan) onPlan?.(r.plan);
    },
    [onPlan],
  );

  const handleOrchestrate = useCallback(
    (r: OrchestrateResult) => {
      if (r.launched && r.runId) {
        setError(null);
        startRunPolling(r.runId);
      }
    },
    [startRunPolling],
  );

  const start = useCallback(() => {
    if (disabled || conn === 'live' || conn === 'connecting' || conn === 'minting') return;
    setError(null);
    const s = new RealtimeVoiceSession(aguiUrl, sessionId, {
      onStatus: setConn,
      onUserTranscript: setUserLine,
      onAssistantTranscript: (t, done) => setAssistantLine((prev) => (done ? t : prev + t)),
      onPlan: handlePlan,
      onOrchestrate: handleOrchestrate,
      onError: setError,
    });
    sessionRef.current = s;
    void s.connect();
  }, [aguiUrl, sessionId, disabled, conn, handlePlan, handleOrchestrate]);

  const stop = useCallback(() => {
    sessionRef.current?.close();
    sessionRef.current = null;
    if (pollRef.current) clearTimeout(pollRef.current);
  }, []);

  useEffect(
    () => () => {
      sessionRef.current?.close();
      if (pollRef.current) clearTimeout(pollRef.current);
    },
    [],
  );

  const live = conn === 'live';
  const busy = conn === 'minting' || conn === 'connecting';
  const turns = runTurns(runState);

  return (
    <div
      aria-label="Voice host"
      style={{
        border: '1px solid var(--hairline)',
        borderRadius: 12,
        background: '#fff',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '10px 14px',
          borderBottom: '1px solid var(--hairline)',
          background: '#FCFBF9',
        }}
      >
        <button
          type="button"
          onClick={live || busy ? stop : start}
          disabled={disabled || busy}
          aria-pressed={live}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 8,
            padding: '7px 14px',
            borderRadius: 9,
            border: live ? '1px solid #B42318' : '1px solid #0F8A82',
            background: live ? '#B42318' : busy ? '#F5F3EF' : '#0F8A82',
            color: live ? '#fff' : busy ? '#A8A299' : '#fff',
            fontWeight: 600,
            fontSize: 13,
            cursor: disabled || busy ? 'not-allowed' : 'pointer',
          }}
        >
          {live ? 'Stop voice' : busy ? 'Connecting…' : 'Start voice'}
        </button>
        <span style={{ fontSize: 12, color: '#6B6461' }}>
          {disabled
            ? 'Backend unreachable — voice disabled'
            : live
              ? `Live · ${awaitingGo ? 'ready to run (say “go”)' : 'interviewing…'}`
              : 'gpt-realtime · ephemeral secret · nothing is sent'}
        </span>
        {live && (
          <span
            aria-hidden
            style={{
              marginLeft: 'auto',
              width: 9,
              height: 9,
              borderRadius: '50%',
              background: '#34D399',
            }}
          />
        )}
      </div>

      {error && (
        <div role="alert" style={{ padding: '8px 14px', color: '#B42318', fontSize: 12 }}>
          {error}
        </div>
      )}

      {/* Live captions: what the operator said + what the host is saying. */}
      {(userLine || assistantLine) && (
        <div style={{ padding: '8px 14px', display: 'flex', flexDirection: 'column', gap: 4 }}>
          {userLine && (
            <div style={{ fontSize: 12, color: '#6B6461' }}>
              <strong>You:</strong> {userLine}
            </div>
          )}
          {assistantLine && (
            <div style={{ fontSize: 12, color: '#0F1A19' }}>
              <strong>Host:</strong> {assistantLine}
            </div>
          )}
        </div>
      )}

      {/* Plan readback (the live shared state the interview is building). */}
      {readback && (
        <div
          style={{
            padding: '8px 14px',
            fontSize: 12.5,
            color: '#1A1A17',
            borderTop: '1px solid var(--hairline)',
            background: awaitingGo ? '#E6F4EC' : '#FCFBF9',
          }}
        >
          <strong>Plan:</strong> {readback}
        </div>
      )}

      {/* On a valid GO: the EXISTING live orchestration surface, reused verbatim. */}
      {(running || (runState && runState.steps.length > 0)) && (
        <OrchestrationFlow turns={turns} running={running} />
      )}
    </div>
  );
}
