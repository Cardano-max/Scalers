'use client';

/**
 * AgencyCanvas — the "Agency at Work" war-room. PRESENTATIONAL: it binds ONLY to a
 * real RunState (run-trace steps written by campaign_runner.py) and renders the run
 * as a cinematic agency narrative — every label, count, and active flag traces to
 * real agent_runs data:
 *
 *   roster (who is working) · lane (the workflow + handoffs) · timeline (real spans,
 *   the evidence of what they produced) · Deep-research citations · the spec artifact.
 *
 * HONESTY: empty/loading run → skeleton + idle CTA, never a fake agent. ×N badges =
 * real counts. Active pulse = the single earliest stage with no landed step. Handoff
 * edges draw only when the downstream step's createdAt appears. Sources = real
 * research_sources or an honest empty state. Nothing here sends.
 */
import { useMemo } from 'react';
import type { RunState } from '@/lib/studio/run-trace';
import {
  deriveAgencyStages,
  extractResearchSources,
  stepSummaryLine,
  type AgencyStage,
} from '@/lib/studio/agency';
import { AGENT_PERSONAS } from '@/lib/studio/persona';
import { StepSpanRow } from './StepSpanRow';
import { ResearchSourcesRail } from './ResearchSourcesRail';
import { SpecArtifactCard } from './SpecArtifactCard';
import { StagedDraftsReview } from './StagedDraftsReview';

const TEAL = '#0F8A82';

export interface AgencyCanvasProps {
  runState: RunState | null;
  running: boolean;
  connected: boolean;
  /** Idle teal CTA — kicks the real /studio/run spine. Omit to hide the CTA. */
  onRunCampaign?: () => void;
  onOpenReview?: () => void;
  /** Open the Review Queue detail focused on ONE staged draft (Deep Review). */
  onDeepReview?: (actionId: string) => void;
  /** Compact mode (embedded beneath the Voice hero) trims the outer chrome. */
  compact?: boolean;
}

export function AgencyCanvas({
  runState,
  running,
  connected,
  onRunCampaign,
  onOpenReview,
  onDeepReview,
  compact = false,
}: AgencyCanvasProps) {
  const stages = useMemo(() => deriveAgencyStages(runState, running), [runState, running]);
  const steps = useMemo(
    () => (runState?.steps ?? []).slice().sort((a, b) => a.seq - b.seq),
    [runState],
  );
  const sources = useMemo(
    () => extractResearchSources(stages.find((s) => s.key === 'research')?.steps ?? []),
    [stages],
  );

  const juryStage = stages.find((s) => s.key === 'jury');
  const juryDone = !!juryStage?.done;
  const completed = runState?.status === 'completed';
  const hasRun = steps.length > 0 || running;
  // Real HELD draft rows for this run. The run transitions into review mode (per-draft
  // Approve / Reject / Deep-Review) once it completes (or the jury has landed) and
  // there are staged drafts — never fabricated; empty list renders nothing.
  const pendingDrafts = runState?.pending ?? [];
  const showReview = (completed || juryDone) && pendingDrafts.length > 0;

  // ── Idle / not-connected states ────────────────────────────────────────────
  if (!hasRun) {
    return (
      <div
        style={{
          minHeight: compact ? 240 : 360,
          display: 'grid',
          placeItems: 'center',
          padding: 32,
          background: 'var(--warroom-canvas)',
          borderRadius: compact ? 0 : 'var(--radius-card)',
        }}
      >
        <div style={{ maxWidth: 460, textAlign: 'center', display: 'flex', flexDirection: 'column', gap: 14, alignItems: 'center' }}>
          <AgencyRosterPreview />
          <h2 style={{ margin: 0, fontSize: 19, fontWeight: 590, letterSpacing: '-0.01em', color: 'var(--ink)' }}>
            The agency is ready
          </h2>
          <p style={{ margin: 0, fontSize: 13.5, lineHeight: 1.55, color: 'var(--text-secondary)' }}>
            On <strong>Run campaign</strong> you watch the team work live: deep research on
            the web → the strategist sets the angle → copywriters draft → critics re-verify
            each draft → the supervising jury evaluates. Every draft is HELD for your approval.
          </p>
          {connected && onRunCampaign ? (
            <button
              type="button"
              onClick={onRunCampaign}
              style={{
                marginTop: 4,
                fontSize: 13.5,
                fontWeight: 600,
                color: '#fff',
                background: TEAL,
                border: 'none',
                padding: '11px 22px',
                borderRadius: 'var(--radius-button)',
                cursor: 'pointer',
                boxShadow: 'var(--shadow-selected)',
              }}
            >
              Run campaign
            </button>
          ) : (
            <p style={{ margin: 0, fontSize: 12, color: 'var(--text-muted)' }}>
              {connected
                ? 'Start a run from the Voice tab or the Command workbench to watch it here.'
                : 'Backend unreachable — this is the honest not-connected state. No run can start.'}
            </p>
          )}
        </div>
      </div>
    );
  }

  const landed = steps.length;

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
        padding: compact ? '16px 0 0' : 0,
        background: compact ? 'transparent' : 'var(--warroom-canvas)',
      }}
    >
      {/* Header — "Orchestrating now" + honest status. */}
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', padding: compact ? '0 2px' : '2px 2px' }}>
        <span
          aria-hidden
          className={running ? 'active-pulse' : undefined}
          style={{
            width: 10,
            height: 10,
            borderRadius: '50%',
            background: running ? TEAL : completed ? 'var(--success-dot)' : 'var(--text-faint)',
            flex: '0 0 auto',
            // @ts-expect-error custom prop for the pulse keyframe color
            '--pulse-color': 'rgba(15,138,130,0.5)',
          }}
        />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 17, fontWeight: 590, letterSpacing: '-0.01em', color: 'var(--ink)' }}>
            {running ? 'Orchestrating now' : completed ? 'Run complete' : 'Agency at work'}
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--text-muted)', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontVariantNumeric: 'tabular-nums' }}>
              {landed} agent{landed === 1 ? '' : 's'} {running ? 'landed' : 'complete'}
            </span>
            {runState?.archetype && (
              <>
                <span style={{ color: 'var(--text-faint)' }}>·</span>
                <span>
                  archetype <span style={{ fontFamily: 'var(--font-mono)' }}>{runState.archetype}</span>
                </span>
              </>
            )}
            {runState?.nPending != null && (
              <>
                <span style={{ color: 'var(--text-faint)' }}>·</span>
                <span style={{ color: 'var(--amber-text)', fontVariantNumeric: 'tabular-nums' }}>
                  {runState.nPending} HELD for approval
                </span>
              </>
            )}
          </div>
        </div>
      </header>

      {runState?.error && (
        <div role="alert" style={{ fontSize: 12.5, color: 'var(--danger-text)', background: 'var(--danger-bg)', border: '1px solid #F1BEB8', borderRadius: 9, padding: '9px 12px' }}>
          Run error: {runState.error}
        </div>
      )}

      {/* The lane / workflow with handoff edges. */}
      <AgencyLane stages={stages} />

      {/* The spec artifact materializes on jury-complete. */}
      {juryDone && runState?.runId && (
        <SpecArtifactCard
          runId={runState.runId}
          nPending={runState.nPending}
          onOpenReview={onOpenReview}
        />
      )}

      {/* Result / review mode: the run is done — surface each real HELD draft with
          Approve / Reject / Deep-Review, right where it was streaming. */}
      {showReview && (
        <StagedDraftsReview pending={pendingDrafts} onDeepReview={onDeepReview} />
      )}

      {/* War-room grid: roster · timeline (evidence) · research. */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: compact ? '1fr' : 'minmax(190px, 220px) minmax(0, 1fr) minmax(240px, 300px)',
          gap: 14,
          alignItems: 'start',
        }}
      >
        {/* Roster — who is working. */}
        <Roster stages={stages} />

        {/* Timeline — the real spans, in order: the evidence of what they produced. */}
        <section
          aria-label="Run timeline"
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            background: '#fff',
            border: '1px solid var(--hairline)',
            borderRadius: 'var(--radius-card)',
            padding: 12,
            boxShadow: 'var(--shadow-card)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '0 2px 2px' }}>
            <h3 style={{ margin: 0, fontSize: 13, fontWeight: 590, color: 'var(--ink)' }}>Timeline</h3>
            <span style={{ flex: 1 }} />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>
              {landed} step{landed === 1 ? '' : 's'}
            </span>
          </div>
          {steps.map((s, i) => (
            <StepSpanRow
              key={`${runState?.runId}_${s.seq}`}
              step={s}
              index={i}
              prevCreatedAt={i > 0 ? steps[i - 1].createdAt : null}
            />
          ))}
          {running && (
            <div
              className="shimmer"
              style={{ height: 46, borderRadius: 9, border: '1px solid var(--hairline)' }}
              aria-label="next agent working"
            />
          )}
        </section>

        {/* Right rail: deep research + jury evidence. */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <ResearchSourcesRail sources={sources} researchRan={!!stages.find((s) => s.key === 'research')?.done} />
          {juryStage?.done && juryStage.steps.length > 0 && (
            <JuryEvidence summary={stepSummaryLine(juryStage.steps[juryStage.steps.length - 1])} />
          )}
        </div>
      </div>
    </div>
  );
}

// ── Lane / workflow strip ────────────────────────────────────────────────────
function AgencyLane({ stages }: { stages: AgencyStage[] }) {
  return (
    <section
      aria-label="Agency workflow lane"
      style={{
        display: 'flex',
        alignItems: 'stretch',
        gap: 0,
        background: '#fff',
        border: '1px solid var(--hairline)',
        borderRadius: 'var(--radius-card)',
        padding: '14px 16px',
        boxShadow: 'var(--shadow-card)',
        overflowX: 'auto',
      }}
    >
      {stages.map((stage, i) => (
        <div key={stage.key} style={{ display: 'flex', alignItems: 'center', flex: 1, minWidth: 132 }}>
          <LaneNode stage={stage} />
          {i < stages.length - 1 && (
            <LaneConnector
              accent={stage.accent}
              /* Edge becomes real when the DOWNSTREAM stage has landed (createdAt). */
              filled={!!stages[i + 1].firstCreatedAt}
            />
          )}
        </div>
      ))}
    </section>
  );
}

function LaneNode({ stage }: { stage: AgencyStage }) {
  const on = stage.done || stage.active;
  return (
    <div
      data-stage={stage.key}
      data-done={stage.done ? 'true' : 'false'}
      data-active={stage.active ? 'true' : 'false'}
      style={{ display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'center', textAlign: 'center', flex: '0 0 auto', width: 120 }}
    >
      <span
        className={stage.active ? 'active-pulse' : undefined}
        style={{
          width: 34,
          height: 34,
          borderRadius: '50%',
          display: 'grid',
          placeItems: 'center',
          fontFamily: 'var(--font-mono)',
          fontSize: 11,
          fontWeight: 600,
          color: on ? '#fff' : 'var(--text-faint)',
          background: on ? stage.accent : 'var(--surface-alt)',
          border: `1px solid ${on ? stage.accent : 'var(--hairline-strong)'}`,
          // @ts-expect-error custom prop for the pulse keyframe color
          '--pulse-color': `${stage.accent}73`,
        }}
      >
        {stage.active ? (
          <span
            className="ring-spin"
            aria-hidden
            style={{ width: 14, height: 14, borderRadius: '50%', border: '1.5px solid rgba(255,255,255,0.4)', borderTopColor: '#fff' }}
          />
        ) : (
          stage.persona.initials
        )}
      </span>
      <span style={{ fontSize: 11.5, fontWeight: 590, lineHeight: 1.2, color: on ? 'var(--ink)' : 'var(--text-muted)' }}>
        {stage.label}
      </span>
      <span style={{ fontSize: 10.5, color: 'var(--text-muted)', minHeight: 13 }}>
        {stage.active ? (
          <span style={{ color: stage.accent }}>{stage.verb}…</span>
        ) : stage.done ? (
          <span style={{ fontVariantNumeric: 'tabular-nums' }}>
            done{stage.countable && stage.count > 0 ? ` · ×${stage.count}` : ''}
          </span>
        ) : (
          'queued'
        )}
      </span>
    </div>
  );
}

function LaneConnector({ accent, filled }: { accent: string; filled: boolean }) {
  return (
    <div style={{ flex: 1, minWidth: 14, height: 2, background: 'var(--hairline-strong)', borderRadius: 2, position: 'relative', overflow: 'hidden' }}>
      <div
        style={{
          position: 'absolute',
          inset: 0,
          width: filled ? '100%' : '0%',
          background: accent,
          transition: 'width 480ms cubic-bezier(0.22,1,0.36,1)',
        }}
      />
    </div>
  );
}

// ── Roster ───────────────────────────────────────────────────────────────────
function Roster({ stages }: { stages: AgencyStage[] }) {
  return (
    <section
      aria-label="Agency roster"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        background: '#fff',
        border: '1px solid var(--hairline)',
        borderRadius: 'var(--radius-card)',
        padding: 12,
        boxShadow: 'var(--shadow-card)',
      }}
    >
      <h3 style={{ margin: '0 0 2px 2px', fontSize: 13, fontWeight: 590, color: 'var(--ink)' }}>Roster</h3>
      {stages.map((stage) => {
        const on = stage.done || stage.active;
        return (
          <div
            key={stage.key}
            style={{
              display: 'flex',
              gap: 9,
              alignItems: 'center',
              padding: '8px 9px',
              borderRadius: 9,
              background: on ? stage.persona.bg : 'var(--surface-alt)',
              border: `1px solid ${on ? stage.persona.border : 'var(--hairline)'}`,
            }}
          >
            <span
              className={stage.active ? 'active-pulse' : undefined}
              style={{
                width: 26,
                height: 26,
                borderRadius: 7,
                display: 'grid',
                placeItems: 'center',
                fontFamily: 'var(--font-mono)',
                fontSize: 10,
                fontWeight: 600,
                color: '#fff',
                background: on ? stage.accent : 'var(--text-faint)',
                flex: '0 0 auto',
                // @ts-expect-error custom prop for the pulse keyframe color
                '--pulse-color': `${stage.accent}73`,
              }}
            >
              {stage.persona.initials}
            </span>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontSize: 12.5, fontWeight: 560, color: on ? 'var(--ink)' : 'var(--text-muted)' }}>
                {stage.persona.name}
                {stage.countable && stage.count > 1 && (
                  <span style={{ fontVariantNumeric: 'tabular-nums', color: stage.accent }}>{`  ×${stage.count}`}</span>
                )}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                {stage.active ? (
                  <span style={{ color: stage.accent }}>{stage.verb}…</span>
                ) : stage.done ? (
                  'done'
                ) : (
                  'queued'
                )}
              </div>
            </div>
          </div>
        );
      })}
    </section>
  );
}

function AgencyRosterPreview() {
  const order = ['researcher', 'strategist', 'draft', 'critic', 'jury'] as const;
  return (
    <div style={{ display: 'flex', gap: -6 }}>
      {order.map((k, i) => {
        const p = AGENT_PERSONAS[k];
        return (
          <span
            key={k}
            style={{
              width: 38,
              height: 38,
              borderRadius: '50%',
              display: 'grid',
              placeItems: 'center',
              fontFamily: 'var(--font-mono)',
              fontSize: 11,
              fontWeight: 600,
              color: '#fff',
              background: p.accent,
              border: '2px solid var(--warroom-canvas)',
              marginLeft: i === 0 ? 0 : -8,
            }}
          >
            {p.initials}
          </span>
        );
      })}
    </div>
  );
}

function JuryEvidence({ summary }: { summary: string }) {
  return (
    <section
      aria-label="Jury evaluation"
      className="spring-in"
      style={{
        background: AGENT_PERSONAS.jury.bg,
        border: `1px solid ${AGENT_PERSONAS.jury.border}`,
        borderRadius: 'var(--radius-card)',
        padding: 12,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span aria-hidden style={{ width: 7, height: 7, borderRadius: '50%', background: AGENT_PERSONAS.jury.accent }} />
        <h3 style={{ margin: 0, fontSize: 13, fontWeight: 590, color: AGENT_PERSONAS.jury.accent }}>
          Supervising jury · evaluated
        </h3>
      </div>
      <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.5, color: 'var(--text-secondary)' }}>{summary}</p>
    </section>
  );
}
