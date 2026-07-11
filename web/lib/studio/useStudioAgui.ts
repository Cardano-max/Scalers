'use client';

/**
 * useStudioAgui — the live Campaign Studio state machine over the AG-UI backend.
 *
 * Owns the real round-trip: it sends operator turns to `POST /studio/agui`, streams
 * the host reply, mirrors the shared-state CampaignPlan, reconciles the labeled
 * role transcript from persisted history, and drives the approval gate (a deferred
 * `stage_publish` surfaces here as `pendingApproval`; approving re-POSTs a resume).
 *
 * HONESTY: on mount it probes the backend. Unreachable → `streamStatus: 'preview'`
 * and every action is a no-op, so the UI shows the honest not-connected state and
 * NEVER a fabricated exchange. Reachable → every turn is a real model round-trip.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type {
  AgentStep,
  ChatTurn,
  StudioStreamStatus,
} from '@/lib/data/studio-adapter';
import {
  type AguiInterrupt,
  type AguiMessage,
  type CampaignPlan,
  type ObservedToolCall,
  assistantToolCallMessage,
  emptyPlan,
  newRunInput,
  probeAgui,
  runAgui,
  userMessage,
} from './agui';
import { fetchStudioHistory } from './studio-history';
import {
  startRun,
  fetchRunState,
  selectArtwork,
  selectCompetitor,
  type RunState,
} from './run-trace';

export interface PendingApproval {
  interrupt: AguiInterrupt;
  call: ObservedToolCall;
  precedingText: string;
}

const STEP_LABEL: Record<string, { label: string; agent: AgentStep['agent'] }> = {
  revise_plan: { label: 'Revise plan (shared state)', agent: 'STRATEGIST' },
  brainstorm_with_roles: { label: 'Brainstorm with role cells', agent: 'STRATEGIST' },
  run_campaign: { label: 'Run campaign — real traced spine (HELD)', agent: 'STRATEGIST' },
  stage_publish: { label: 'Stage publish — approval required', agent: 'SAFETY' },
};

/** Persisted thread roles that represent a completed PIPELINE step (vs the
 *  operator or the conversational host). Used to rebuild the live-progress strip
 *  from history so a finished run survives a tab switch. */
const PIPELINE_STEP_ROLES = new Set<ChatTurn['role']>([
  'RESEARCHER',
  'STRATEGIST',
  'COPYWRITER',
  'CRITIC',
  'JURY',
]);

/**
 * Re-derive the live-progress steps from a persisted thread. Each persisted
 * agent-role trace (strategist, draft, critic, jury, …) becomes a DONE step so
 * returning to the Command tab shows the run's progress again instead of an empty
 * panel — the steps array is otherwise in-memory only and lost on unmount.
 * Operator and conversational-host turns are not pipeline steps.
 */
export function deriveStepsFromHistory(turns: ChatTurn[]): AgentStep[] {
  const steps: AgentStep[] = [];
  for (const t of turns) {
    if (!PIPELINE_STEP_ROLES.has(t.role)) continue;
    steps.push({
      id: `hist_${t.id}`,
      agent: t.role,
      label: t.label || t.role,
      status: 'done',
    });
  }
  return steps;
}

export interface UseStudioAgui {
  connected: boolean | null;
  streamStatus: StudioStreamStatus;
  turns: ChatTurn[];
  plan: CampaignPlan;
  planVersion: number;
  planDirty: boolean;
  steps: AgentStep[];
  busy: boolean;
  /** True while a deterministic button-triggered campaign run is in flight. */
  runningCampaign: boolean;
  /** Live state of the current/last run — per-agent steps as they land (drives the
   *  OrchestrationFlow stepper + per-agent cards filling in real time). */
  runState: RunState | null;
  pendingApproval: PendingApproval | null;
  error: string | null;
  send: (text: string) => void;
  /**
   * Record a finalized SPOKEN turn into the SAME transcript as typed turns, so the
   * voice host and the text composer are ONE conversation (not two windows). These
   * client-recorded lines are kept separately from the persisted/streamed history so
   * a history refresh (after a typed turn or a completed run) never wipes them; the
   * exposed `turns` is the two merged in chronological order. The session id is the
   * SAME one the voice WebRTC session mints with — voice + text share one session.
   */
  recordVoiceTurn: (role: ChatTurn['role'], label: string, text: string) => void;
  setPlanField: (patch: Partial<CampaignPlan>) => void;
  applyEditsAndReplan: () => void;
  /** Deterministic "Run campaign" — POST /studio/run (returns run_id fast), then
   *  poll GET /studio/run/{id} so per-agent steps surface live, not batch-revealed. */
  runCampaign: () => void;
  /** Begin polling a run launched elsewhere (e.g. the voice GO-gate) so the shared
   *  reasoning stream renders its real per-agent steps. Does NOT start a run itself. */
  attachRun: (runId: string) => void;
  /** Resolve a paused run's artwork pick (status 'awaiting_selection'): POST
   *  select-artwork with the chosen assetId; polling continues and the run resumes. */
  pickArtwork: (assetId: string) => void;
  /** Resolve a paused run's competitor pick: POST select-competitor with the chosen
   *  optionId; polling continues and the run resumes (mirror of pickArtwork). */
  pickCompetitor: (optionId: string) => void;
  approve: () => void;
  reject: () => void;
}

export function useStudioAgui(
  aguiUrl: string,
  graphqlUrl: string,
  sessionId: string,
): UseStudioAgui {
  const [connected, setConnected] = useState<boolean | null>(null);
  const [streamStatus, setStreamStatus] = useState<StudioStreamStatus>('connecting');
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  // Finalized spoken turns, recorded client-side so they interleave with the typed
  // transcript and SURVIVE a history refresh (which replaces `turns`). Kept separate
  // and merged on read — see `recordVoiceTurn` and the merged `turns` returned below.
  const [voiceTurns, setVoiceTurns] = useState<ChatTurn[]>([]);
  const [plan, setPlan] = useState<CampaignPlan>(emptyPlan());
  const [planVersion, setPlanVersion] = useState(0);
  const [planDirty, setPlanDirty] = useState(false);
  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [busy, setBusy] = useState(false);
  const [runningCampaign, setRunningCampaign] = useState(false);
  const [runState, setRunState] = useState<RunState | null>(null);
  const [pendingApproval, setPendingApproval] = useState<PendingApproval | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The real AG-UI LLM message history (user + assistant turns). Display turns are
  // reconciled separately from persisted history; this drives conversational continuity.
  const messagesRef = useRef<AguiMessage[]>([]);
  const planRef = useRef<CampaignPlan>(plan);
  planRef.current = plan;

  // Restore the persisted thread + run state on mount, THEN probe reachability.
  //
  // The Command tab UNMOUNTS when the operator navigates away (AppShell mounts only
  // the active screen), so on return this effect is the only thing that rebuilds the
  // studio. The persisted transcript is a plain read — it must NOT be gated on the
  // slow, side-effectful AG-UI probe, or a flaky/slow probe leaves the operator on a
  // blank "no messages" studio even though the whole run is on disk. So we restore
  // the conversation AND the live-progress steps FIRST, then probe purely for the
  // connected/preview banner and to (re-)enable send/run.
  useEffect(() => {
    const ctl = new AbortController();
    let cancelled = false;
    // Each session is its OWN conversation (Claude-style): switching sessions
    // clears the previous session's thread/steps/voice lines before hydrating.
    setTurns([]);
    setVoiceTurns([]);
    setSteps([]);
    messagesRef.current = [];
    lastVoiceRef.current = null;
    (async () => {
      try {
        const history = await fetchStudioHistory(graphqlUrl, sessionId, ctl.signal);
        if (!cancelled && history.length > 0) {
          setTurns(history);
          // Only seed steps from history if a live run hasn't already populated them.
          setSteps((prev) => (prev.length === 0 ? deriveStepsFromHistory(history) : prev));
        }
      } catch {
        /* history is best-effort; an empty thread is still honest */
      }
      const ok = await probeAgui(aguiUrl, ctl.signal);
      if (cancelled) return;
      setConnected(ok);
      setStreamStatus(ok ? 'open' : 'preview');
    })();
    return () => {
      cancelled = true;
      ctl.abort();
    };
  }, [aguiUrl, graphqlUrl, sessionId]);

  const refreshHistory = useCallback(async () => {
    try {
      const history = await fetchStudioHistory(graphqlUrl, sessionId);
      setTurns(history);
    } catch {
      /* keep what we have */
    }
  }, [graphqlUrl, sessionId]);

  const addStepsFromCalls = useCallback((calls: ObservedToolCall[]) => {
    if (calls.length === 0) return;
    setSteps((prev) => {
      const next = [...prev];
      for (const c of calls) {
        const meta = STEP_LABEL[c.name] ?? { label: c.name, agent: 'SYSTEM' as const };
        next.push({
          id: `${c.id}`,
          agent: meta.agent,
          label: meta.label,
          status: c.name === 'stage_publish' ? 'blocked' : 'done',
          detail: c.name === 'stage_publish' ? 'held for operator approval' : undefined,
        });
      }
      return next;
    });
  }, []);

  // Drive one streamed run. `liveTurnId` is the in-flight host bubble to grow.
  const drive = useCallback(
    async (messages: AguiMessage[], resume?: Parameters<typeof newRunInput>[3]) => {
      const liveTurnId = `live_${Date.now()}`;
      let started = false;
      const result = await runAgui(
        aguiUrl,
        newRunInput(sessionId, messages, planRef.current, resume),
        {
          onHostDelta: (delta) => {
            setTurns((prev) => {
              if (!started) {
                started = true;
                return [
                  ...prev,
                  {
                    id: liveTurnId,
                    role: 'SYSTEM',
                    label: 'Studio Host',
                    text: delta,
                    at: new Date().toISOString(),
                    streaming: true,
                  },
                ];
              }
              return prev.map((t) =>
                t.id === liveTurnId ? { ...t, text: t.text + delta } : t,
              );
            });
          },
          onState: (p) => {
            setPlan(p);
            setPlanVersion((v) => v + 1);
            setPlanDirty(false);
          },
        },
      );
      addStepsFromCalls(result.toolCalls);
      return result;
    },
    [aguiUrl, sessionId, addStepsFromCalls],
  );

  const send = useCallback(
    (text: string) => {
      if (!connected || busy) return;
      const trimmed = text.trim();
      if (!trimmed) return;
      setBusy(true);
      setError(null);
      const userMsg = userMessage(trimmed);
      // optimistic operator bubble (reconciled from persisted history afterward)
      setTurns((prev) => [
        ...prev,
        { id: userMsg.id, role: 'OPERATOR', label: 'You', text: trimmed, at: new Date().toISOString() },
      ]);
      const nextMessages = [...messagesRef.current, userMsg];
      (async () => {
        try {
          const result = await drive(nextMessages);
          if (result.error) {
            setError(result.error);
            setStreamStatus('error');
          }
          messagesRef.current = nextMessages;
          if (result.interrupts.length > 0) {
            // Approval gate: pause. Keep the proposed tool call for the resume.
            const intr = result.interrupts[0];
            const call =
              result.toolCalls.find((c) => c.id === intr.toolCallId) ??
              result.toolCalls[result.toolCalls.length - 1];
            if (call) {
              setPendingApproval({ interrupt: intr, call, precedingText: result.hostText });
            }
          } else if (result.hostText) {
            messagesRef.current = [
              ...nextMessages,
              { id: `a_${Date.now()}`, role: 'assistant', content: result.hostText },
            ];
          }
          await refreshHistory();
        } catch {
          // Transport failure mid-session: degrade honestly, drop the optimistic bubble.
          setStreamStatus('preview');
          setConnected(false);
          setTurns((prev) => prev.filter((t) => t.id !== userMsg.id));
        } finally {
          setBusy(false);
        }
      })();
    },
    [connected, busy, drive, refreshHistory],
  );

  const resolveApproval = useCallback(
    (approved: boolean) => {
      const pending = pendingApproval;
      if (!pending || busy || !connected) return;
      setBusy(true);
      setPendingApproval(null);
      const toolMsg = assistantToolCallMessage(pending.call, pending.precedingText);
      const messages = [...messagesRef.current, toolMsg];
      (async () => {
        try {
          const result = await drive(messages, [
            { interruptId: pending.interrupt.id, status: 'resolved', payload: { approved } },
          ]);
          if (result.error) {
            setError(result.error);
            setStreamStatus('error');
          }
          messagesRef.current = result.hostText
            ? [...messages, { id: `a_${Date.now()}`, role: 'assistant', content: result.hostText }]
            : messages;
          await refreshHistory();
        } catch {
          setStreamStatus('preview');
          setConnected(false);
        } finally {
          setBusy(false);
        }
      })();
    },
    [pendingApproval, busy, connected, drive, refreshHistory],
  );

  // Record a finalized spoken line into the unified transcript. Deduped against the
  // immediately-previous voice line (the realtime stream can re-emit a final), so a
  // single utterance lands once. Never sends anything — it is a display record only.
  const lastVoiceRef = useRef<{ role: string; text: string } | null>(null);
  const recordVoiceTurn = useCallback(
    (role: ChatTurn['role'], label: string, text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      // Dedupe re-emitted finals BEFORE any side effect (persist/thread-join).
      const last = lastVoiceRef.current;
      if (last && last.role === role && last.text === trimmed) return;
      lastVoiceRef.current = { role, text: trimmed };
      setVoiceTurns((prev) => [
        ...prev,
        {
          id: `voice_${role}_${Date.now()}_${prev.length}`,
          role,
          label,
          text: trimmed,
          at: new Date().toISOString(),
        },
      ]);
      // ONE conversation across voice and text: the spoken line joins the typed
      // host's message thread (so the next typed turn carries it as real context)…
      const isOperator = role === 'OPERATOR';
      messagesRef.current = [
        ...messagesRef.current,
        {
          id: `v_${Date.now()}`,
          role: isOperator ? 'user' : 'assistant',
          content: trimmed,
        } as AguiMessage,
      ];
      // …and persists to studio_chat_turns, so a re-minted voice session (and the
      // session list / hydration) reads it back. Fire-and-forget; display already done.
      if (aguiUrl) {
        fetch(`${aguiUrl.replace(/\/agui(\?.*)?$/, '')}/voice/turn`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            sessionId,
            role: isOperator ? 'operator' : 'host',
            text: trimmed,
          }),
        }).catch(() => {});
      }
    },
    [aguiUrl, sessionId],
  );

  // The ONE transcript: persisted/streamed turns + client-recorded voice turns,
  // merged in chronological order (ISO timestamps sort lexically). This is what the
  // single Voice surface renders — typed and spoken lines in one conversation.
  const mergedTurns = useMemo<ChatTurn[]>(() => {
    if (voiceTurns.length === 0) return turns;
    // Spoken lines now ALSO persist server-side; once history returns them, drop
    // the client-side copy so a single utterance never renders twice.
    const persisted = new Set(turns.map((t) => `${t.role}|${t.text.trim()}`));
    const pendingVoice = voiceTurns.filter(
      (v) => !persisted.has(`${v.role}|${v.text.trim()}`),
    );
    if (pendingVoice.length === 0) return turns;
    return [...turns, ...pendingVoice].sort((a, b) =>
      a.at < b.at ? -1 : a.at > b.at ? 1 : 0,
    );
  }, [turns, voiceTurns]);

  const setPlanField = useCallback((patch: Partial<CampaignPlan>) => {
    setPlan((prev) => ({ ...prev, ...patch }));
    setPlanDirty(true);
  }, []);

  const applyEditsAndReplan = useCallback(() => {
    if (!connected || busy) return;
    // The edited plan is carried in `state` on the next run; ask the host to re-plan
    // around the operator's edits. The backend loads the edited state into deps and
    // persists it, so this both SYNCS the edit and triggers a real re-plan.
    send(
      'I edited the campaign plan fields directly. Re-plan around my changes and ' +
        'confirm the updated goal, audience, and channels.',
    );
  }, [connected, busy, send]);

  // DETERMINISTIC LIVE run: POST /studio/run (returns the run_id fast — the real traced
  // spine runs in the backend BACKGROUND), then poll GET /studio/run/{id} every ~1.5s so
  // the per-agent steps surface AS THEY LAND. `runState` drives the OrchestrationFlow
  // stepper + the per-agent cards filling in real time, instead of a ~60s batch reveal.
  // On completion we refresh history once so the persisted operator trigger + per-agent
  // traces + host summary replace the live placeholders. NOTHING sends (HELD/PENDING).
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Shared poll loop: GET /studio/run/{id} every ~2s, surfacing per-agent steps as
  // they land, until the run completes/errors or a no-progress safety cap. Used by
  // both the button-triggered runCampaign and attachRun (a run launched elsewhere,
  // e.g. the voice GO-gate) so a single, honest poller drives the live stream.
  //
  // The cap is PROGRESS-AWARE: any newly-landed step resets it (long multi-agent
  // runs keep streaming), and a run paused on an operator pick (awaiting_selection)
  // never times out — the operator can take as long as they need to choose.
  const pollRun = useCallback(
    (runId: string) => {
      if (pollRef.current) clearTimeout(pollRef.current);
      let stalls = 0;
      let lastProgress = -1;
      const poll = async () => {
        try {
          const st = await fetchRunState(aguiUrl, runId);
          setRunState(st);
          if (st.status === 'completed' || st.status === 'error') {
            setRunningCampaign(false);
            setBusy(false);
            if (st.status === 'error' && st.error) setError(st.error);
            // Surface the operator trigger + per-agent turns + host summary in the chat.
            await refreshHistory();
            return;
          }
          if (st.steps.length !== lastProgress || st.status === 'awaiting_selection') {
            lastProgress = st.steps.length;
            stalls = 0;
          } else {
            stalls += 1;
          }
        } catch {
          // transient poll error — count as a stall; keep polling until the cap.
          stalls += 1;
        }
        if (stalls >= 90) {
          // ~3 min with no landed step and no operator pause: stop polling — but
          // NEVER silently. A zero-step stall used to quietly flip the panel back
          // to its idle state ("loading… then everything vanished", a real
          // operator report); now the failure stays on screen with the likely
          // causes, and any steps that DID land are kept (honest stall, not a
          // fake completion and not a silent reset).
          setRunningCampaign(false);
          setBusy(false);
          setRunState((prev) => {
            const landed = prev?.steps.length ?? 0;
            const msg =
              landed === 0
                ? `The run never produced a step (run ${runId || 'unknown'}). ` +
                  'Most common causes: the engine process stopped, or its model key ' +
                  '(ANTHROPIC_API_KEY in engine/.env) is missing — check the engine ' +
                  'window, fix, and launch again.'
                : 'The run stopped making progress — the steps that landed are kept. ' +
                  'Check the Runs tab or the engine window for the rest.';
            setError(msg);
            return prev ? { ...prev, status: 'error', error: msg } : prev;
          });
          return;
        }
        pollRef.current = setTimeout(poll, 2000);
      };
      pollRef.current = setTimeout(poll, 1000);
    },
    [aguiUrl, refreshHistory],
  );

  const runCampaign = useCallback(() => {
    if (!connected || busy || runningCampaign) return;
    setRunningCampaign(true);
    setBusy(true);
    setError(null);
    setRunState({ runId: '', status: 'running', steps: [], nPending: null, pending: [], archetype: null, error: null });

    (async () => {
      let runId: string;
      try {
        const r = await startRun(aguiUrl, sessionId, planRef.current);
        runId = r.runId;
        setRunState((prev) => ({ ...(prev as RunState), runId }));
      } catch (e) {
        setError(e instanceof Error ? e.message : 'run start failed');
        setRunningCampaign(false);
        setBusy(false);
        setRunState((prev) => (prev ? { ...prev, status: 'error', error: 'run start failed' } : prev));
        return;
      }
      pollRun(runId);
    })();
  }, [connected, busy, runningCampaign, aguiUrl, sessionId, pollRun]);

  // Attach to a run that was launched OUTSIDE this hook — specifically the voice
  // GO-gate (POST /studio/voice/orchestrate launches the same held spine server-side
  // and returns a runId). We did NOT start it, so we only begin polling: the shared
  // reasoning stream then fills with the SAME real agent_runs the Command run uses.
  const attachRun = useCallback(
    (runId: string) => {
      if (!runId || runningCampaign) return;
      setRunningCampaign(true);
      setBusy(true);
      setError(null);
      setRunState({ runId, status: 'running', steps: [], nPending: null, pending: [], archetype: null, error: null });
      pollRun(runId);
    },
    [runningCampaign, pollRun],
  );

  // Resolve a paused run's artwork pick (spec section 22). POSTs the REAL assetId to
  // select-artwork; on success the pause is cleared optimistically and polling
  // re-arms immediately so the resumed steps stream in without a dead gap.
  const pickArtwork = useCallback(
    (assetId: string) => {
      const runId = runState?.runId;
      if (!runId || !assetId) return;
      setError(null);
      (async () => {
        try {
          await selectArtwork(aguiUrl, runId, assetId);
          setRunState((prev) =>
            prev ? { ...prev, status: 'running', selectionRequest: null } : prev,
          );
          setRunningCampaign(true);
          pollRun(runId);
        } catch (e) {
          setError(e instanceof Error ? e.message : 'artwork selection failed');
        }
      })();
    },
    [aguiUrl, runState?.runId, pollRun],
  );

  // Resolve a paused run's COMPETITOR pick — the competitor-research counterpart of
  // pickArtwork. POSTs the REAL optionId to select-competitor; on success the pause is
  // cleared optimistically and polling re-arms so the resumed steps stream in.
  const pickCompetitor = useCallback(
    (optionId: string) => {
      const runId = runState?.runId;
      if (!runId || !optionId) return;
      setError(null);
      (async () => {
        try {
          await selectCompetitor(aguiUrl, runId, optionId);
          setRunState((prev) =>
            prev ? { ...prev, status: 'running', competitorSelectionRequest: null } : prev,
          );
          setRunningCampaign(true);
          pollRun(runId);
        } catch (e) {
          setError(e instanceof Error ? e.message : 'competitor selection failed');
        }
      })();
    },
    [aguiUrl, runState?.runId, pollRun],
  );

  // Stop polling on unmount.
  useEffect(() => () => {
    if (pollRef.current) clearTimeout(pollRef.current);
  }, []);

  return {
    connected,
    streamStatus,
    turns: mergedTurns,
    plan,
    planVersion,
    planDirty,
    steps,
    busy,
    runningCampaign,
    runState,
    pendingApproval,
    error,
    send,
    recordVoiceTurn,
    setPlanField,
    applyEditsAndReplan,
    runCampaign,
    attachRun,
    pickArtwork,
    pickCompetitor,
    approve: () => resolveApproval(true),
    reject: () => resolveApproval(false),
  };
}
