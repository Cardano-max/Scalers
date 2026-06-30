'use client';

/**
 * OrchestrationFlow — a compact horizontal pipeline above the conversation:
 *
 *   Host → Strategist → Drafts(×N) → Critics(×N) → Jury
 *
 * Each stage lights up (its persona accent) once at least one turn from that
 * stage has appeared in the thread; stages that haven't run yet stay muted. The
 * counts (×N) are REAL — derived from the actual turns — so the strip is an
 * honest at-a-glance map of how far the multi-agent run has progressed, not a
 * decorative animation.
 */
import { AGENT_PERSONAS } from '@/lib/studio/persona';
import { studioPersona } from '@/lib/studio/persona';
import type { ChatTurn } from '@/lib/data/studio-adapter';

export interface FlowStage {
  key: string;
  label: string;
  accent: string;
  /** Number of turns seen for this stage (used for the ×N badge when countable). */
  count: number;
  /** Whether to surface the ×N badge (drafts / critics fan out; others are 1). */
  countable: boolean;
  done: boolean;
  /** The stage currently in flight during a LIVE run — shows a spinner. At most one
   *  stage is active; it is the earliest pipeline stage not yet landed while running. */
  active: boolean;
}

/**
 * Derive the pipeline stages + their done/count from the real thread. A turn's
 * persona key (label-first identity from PART 1) decides which stage it belongs to.
 *
 * During a LIVE run (`running`), the earliest worker stage that has NOT landed yet is
 * marked `active` so the strip shows a spinner moving Strategist → Drafts → Critics →
 * Jury as each agent finishes — the honest live progression, not a decorative loop.
 */
export function deriveFlowStages(turns: ChatTurn[], running = false): FlowStage[] {
  let host = 0;
  let strategy = 0;
  let drafts = 0;
  let critics = 0;
  let jury = 0;

  for (const turn of turns) {
    switch (studioPersona(turn).key) {
      case 'host':
        host += 1;
        break;
      case 'strategist':
      case 'funnel':
      case 'researcher':
        strategy += 1;
        break;
      case 'draft':
      case 'copywriter':
        drafts += 1;
        break;
      case 'critic':
        critics += 1;
        break;
      case 'jury':
        jury += 1;
        break;
      default:
        break;
    }
  }

  const stages: FlowStage[] = [
    { key: 'host', label: 'Host', accent: AGENT_PERSONAS.host.accent, count: host, countable: false, done: host > 0, active: false },
    { key: 'strategist', label: 'Strategist', accent: AGENT_PERSONAS.strategist.accent, count: strategy, countable: false, done: strategy > 0, active: false },
    { key: 'drafts', label: 'Drafts', accent: AGENT_PERSONAS.draft.accent, count: drafts, countable: true, done: drafts > 0, active: false },
    { key: 'critics', label: 'Critics', accent: AGENT_PERSONAS.critic.accent, count: critics, countable: true, done: critics > 0, active: false },
    { key: 'jury', label: 'Jury', accent: AGENT_PERSONAS.jury.accent, count: jury, countable: false, done: jury > 0, active: false },
  ];

  if (running) {
    // The active stage is the earliest WORKER stage (host excluded — it does not run
    // in the deterministic spine) that has not produced a turn yet.
    const next = stages.find((s) => s.key !== 'host' && !s.done);
    if (next) next.active = true;
  }

  return stages;
}

export function OrchestrationFlow({
  turns,
  running = false,
}: {
  turns: ChatTurn[];
  /** True while a live run is in flight — lights the active stage with a spinner. */
  running?: boolean;
}) {
  const stages = deriveFlowStages(turns, running);

  return (
    <div
      aria-label="Campaign orchestration flow"
      style={{
        display: 'flex',
        alignItems: 'center',
        flexWrap: 'wrap',
        gap: 4,
        padding: '8px 16px',
        borderBottom: '1px solid var(--hairline)',
        background: '#FCFBF9',
      }}
    >
      {/* Self-contained spinner keyframes so the active-stage indicator never depends
          on a global stylesheet being present. */}
      <style>{'@keyframes studioFlowSpin{to{transform:rotate(360deg)}}'}</style>
      {stages.map((stage, i) => (
        <div key={stage.key} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span
            data-stage={stage.key}
            data-done={stage.done ? 'true' : 'false'}
            data-active={stage.active ? 'true' : 'false'}
            title={
              stage.active
                ? `${stage.label}: running…`
                : stage.done
                  ? `${stage.label}: ${stage.count} turn${stage.count === 1 ? '' : 's'}`
                  : `${stage.label}: not run yet`
            }
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '3px 9px',
              borderRadius: 999,
              fontSize: 11.5,
              fontWeight: 600,
              border: `1px solid ${stage.done || stage.active ? stage.accent : '#E2DED5'}`,
              background: stage.done ? `${stage.accent}14` : stage.active ? `${stage.accent}0A` : '#fff',
              color: stage.done || stage.active ? stage.accent : '#A8A299',
            }}
          >
            {stage.active ? (
              <span
                aria-label="running"
                style={{
                  width: 9,
                  height: 9,
                  borderRadius: '50%',
                  border: `1.5px solid ${stage.accent}40`,
                  borderTopColor: stage.accent,
                  animation: 'studioFlowSpin 0.7s linear infinite',
                }}
              />
            ) : (
              <span
                aria-hidden
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: '50%',
                  background: stage.done ? stage.accent : '#D6D1C8',
                }}
              />
            )}
            {stage.label}
            {stage.countable && stage.count > 0 && (
              <span style={{ fontVariantNumeric: 'tabular-nums', opacity: 0.85 }}>
                ×{stage.count}
              </span>
            )}
          </span>
          {i < stages.length - 1 && (
            <span aria-hidden style={{ color: '#CFC9BE', fontSize: 12 }}>
              →
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
