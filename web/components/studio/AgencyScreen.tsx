'use client';

/**
 * AgencyScreen — the full-screen "Agency at Work" view. It renders the SAME shared
 * run as the Voice tab's right rail (StudioRunProvider), so a run you kicked off by
 * voice continues to play here without rebuilding.
 *
 * THE GATE (P1a): before any run has started this screen shows the scoping INTERVIEW
 * (AgencyInterview) — the supervisor gathers goal / audience / channels / type /
 * output count (then optional refinements) and the Run button stays LOCKED until the
 * engine reports the plan is armed. It never runs blindly off a bare button. Once a
 * run is live (or finished) it swaps to the AgencyCanvas war-room: deep research →
 * strategist → copywriters → critics → jury → the staged drafts (each deep-linking to
 * its EXACT Review Queue row). Nothing here sends.
 */
import { useCallback, useEffect, useState } from 'react';
import { useConsole } from '@/state/console-store';
import { useSharedStudio } from '@/lib/studio/StudioRunProvider';
import { AgencyCanvas } from './AgencyCanvas';
import { AgencyInterview } from './AgencyInterview';
import { BlueprintBoardPanel } from './BlueprintBoardPanel';
import { RunNarration } from './RunNarration';
import {
  deriveInterview,
  postInterview,
  type InterviewResponse,
  type InterviewState,
} from '@/lib/studio/interview';
import type { CampaignPlan } from '@/lib/studio/agui';

function toState(r: InterviewResponse): InterviewState {
  return {
    armed: r.armed,
    missing: r.missing,
    collected: r.collected,
    nextQuestion: r.nextQuestion,
    readyMessage: r.readyMessage,
    gatingFields: r.gatingFields,
    mode: r.mode,
    modeLabel: r.modeLabel,
    plannedSteps: r.plannedSteps,
    planSummary: r.planSummary,
  };
}

export function AgencyScreen() {
  const studio = useSharedStudio();
  const { navigate } = useConsole();
  const connected = studio.connected === true;
  const { aguiUrl, sessionId, setPlanField, runCampaign } = studio;

  const [interview, setInterview] = useState<InterviewState | null>(null);
  const [interviewBusy, setInterviewBusy] = useState(false);

  // A run has started (live or finished) once the spine has emitted any step OR the
  // launch is in flight — at that point we show the war-room instead of the interview.
  const stepCount = studio.runState?.steps?.length ?? 0;
  const hasRun = studio.runningCampaign || stepCount > 0;

  // Load the authoritative gate state once connected (and only while not mid-run).
  useEffect(() => {
    if (!connected || !aguiUrl || hasRun) return;
    let cancelled = false;
    const ctl = new AbortController();
    (async () => {
      try {
        const r = await postInterview(aguiUrl, sessionId, {}, ctl.signal);
        if (!cancelled) setInterview(toState(r));
      } catch {
        // Honest local mirror over whatever plan we already have — never a fake gate.
        if (!cancelled) setInterview(deriveInterview(studio.plan));
      }
    })();
    return () => {
      cancelled = true;
      ctl.abort();
    };
    // studio.plan intentionally excluded: the server response is the source of truth
    // and re-fetching on every plan keystroke would clobber in-flight answers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected, aguiUrl, sessionId, hasRun]);

  const answer = useCallback(
    async (field: string, value: string) => {
      if (!aguiUrl) return;
      setInterviewBusy(true);
      try {
        const r = await postInterview(aguiUrl, sessionId, { [field]: value });
        setInterview(toState(r));
        // Sync the hook plan to the authoritative one so the held /studio/run launch
        // posts exactly these fields (its override merge must not wipe interview data).
        setPlanField(r.plan as Partial<CampaignPlan>);
      } catch {
        /* keep the prior gate state; the operator can retry the answer */
      } finally {
        setInterviewBusy(false);
      }
    },
    [aguiUrl, sessionId, setPlanField],
  );

  return (
    <section
      aria-label="Agency at work"
      style={{ position: 'absolute', inset: 0, overflow: 'auto', padding: 24, background: 'var(--warroom-canvas)' }}
    >
      <div style={{ maxWidth: 1180, margin: '0 auto' }}>
        {hasRun ? (
          <>
            {/* Live host narration of the run, derived from REAL recorded steps (#11). */}
            <RunNarration runState={studio.runState} running={studio.runningCampaign} />
            {/* P1.5: the planner's executable blueprint + the durable progress board —
                the plan-first surface, rendered BEFORE the war-room lanes so the planner
                step reads as the first step. Real backend data only. */}
            <BlueprintBoardPanel
              blueprint={studio.runState?.blueprint}
              board={studio.runState?.board}
            />
            <AgencyCanvas
              runState={studio.runState}
              running={studio.runningCampaign}
              connected={connected}
              onOpenReview={() => navigate('review')}
              onDeepReview={(actionId) => navigate('review', actionId)}
              onPickArtwork={studio.pickArtwork}
            />
          </>
        ) : (
          <AgencyInterview
            state={interview}
            busy={interviewBusy}
            connected={connected}
            running={studio.runningCampaign}
            onAnswer={answer}
            onRun={runCampaign}
          />
        )}
      </div>
    </section>
  );
}
