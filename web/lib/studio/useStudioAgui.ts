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
import { useCallback, useEffect, useRef, useState } from 'react';
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

export interface PendingApproval {
  interrupt: AguiInterrupt;
  call: ObservedToolCall;
  precedingText: string;
}

const STEP_LABEL: Record<string, { label: string; agent: AgentStep['agent'] }> = {
  revise_plan: { label: 'Revise plan (shared state)', agent: 'STRATEGIST' },
  brainstorm_with_roles: { label: 'Brainstorm with role cells', agent: 'STRATEGIST' },
  stage_publish: { label: 'Stage publish — approval required', agent: 'SAFETY' },
};

export interface UseStudioAgui {
  connected: boolean | null;
  streamStatus: StudioStreamStatus;
  turns: ChatTurn[];
  plan: CampaignPlan;
  planVersion: number;
  planDirty: boolean;
  steps: AgentStep[];
  busy: boolean;
  pendingApproval: PendingApproval | null;
  error: string | null;
  send: (text: string) => void;
  setPlanField: (patch: Partial<CampaignPlan>) => void;
  applyEditsAndReplan: () => void;
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
  const [plan, setPlan] = useState<CampaignPlan>(emptyPlan());
  const [planVersion, setPlanVersion] = useState(0);
  const [planDirty, setPlanDirty] = useState(false);
  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [busy, setBusy] = useState(false);
  const [pendingApproval, setPendingApproval] = useState<PendingApproval | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The real AG-UI LLM message history (user + assistant turns). Display turns are
  // reconciled separately from persisted history; this drives conversational continuity.
  const messagesRef = useRef<AguiMessage[]>([]);
  const planRef = useRef<CampaignPlan>(plan);
  planRef.current = plan;

  // Probe reachability + restore persisted transcript on mount.
  useEffect(() => {
    const ctl = new AbortController();
    let cancelled = false;
    (async () => {
      const ok = await probeAgui(aguiUrl, ctl.signal);
      if (cancelled) return;
      setConnected(ok);
      if (!ok) {
        setStreamStatus('preview');
        return;
      }
      setStreamStatus('open');
      try {
        const history = await fetchStudioHistory(graphqlUrl, sessionId, ctl.signal);
        if (!cancelled) setTurns(history);
      } catch {
        /* history is best-effort; an empty thread is still honest */
      }
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

  return {
    connected,
    streamStatus,
    turns,
    plan,
    planVersion,
    planDirty,
    steps,
    busy,
    pendingApproval,
    error,
    send,
    setPlanField,
    applyEditsAndReplan,
    approve: () => resolveApproval(true),
    reject: () => resolveApproval(false),
  };
}
