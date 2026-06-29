'use client';

/**
 * LiveProgressPanel — the per-agent step strip of the Campaign Studio.
 *
 * Shows each agent step (Research, Ideate, Draft, Jury, Route…) as it runs, with
 * a status indicator and an optional one-line detail. The parent owns the step
 * array and updates it from the studio stream.
 *
 * HONESTY: presentational. With no live backend the step list is EMPTY and the
 * panel says so — it does not animate a fake pipeline or pretend agents are at
 * work.
 */
import {
  STUDIO_ROLE_COLOR,
  STUDIO_ROLE_LABEL,
  type AgentStep,
  type AgentStepStatus,
  type StudioStreamStatus,
} from '@/lib/data/studio-adapter';

interface LiveProgressPanelProps {
  steps: AgentStep[];
  streamStatus: StudioStreamStatus;
}

const STATUS_STYLE: Record<AgentStepStatus, { color: string; glyph: string; label: string }> = {
  pending: { color: '#A8A299', glyph: '○', label: 'Pending' },
  running: { color: '#0F8A82', glyph: '◐', label: 'Running' },
  done: { color: '#1A9D5E', glyph: '●', label: 'Done' },
  failed: { color: '#B42318', glyph: '✕', label: 'Failed' },
  blocked: { color: '#9A6B00', glyph: '⏸', label: 'Blocked' },
};

export function LiveProgressPanel({ steps, streamStatus }: LiveProgressPanelProps) {
  const isPreview = streamStatus === 'preview';

  return (
    <section
      aria-label="Live agent progress"
      style={{
        display: 'flex',
        flexDirection: 'column',
        background: '#fff',
        border: '1px solid var(--hairline)',
        borderRadius: 12,
        overflow: 'hidden',
      }}
    >
      <header
        style={{
          padding: '12px 16px',
          borderBottom: '1px solid var(--hairline)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: '#1A1A17' }}>
          Live progress
        </h2>
        <span style={{ fontSize: 11, color: '#A8A299' }}>
          {steps.length > 0 ? `${steps.length} step${steps.length === 1 ? '' : 's'}` : 'idle'}
        </span>
      </header>

      <div
        role="list"
        style={{
          padding: steps.length === 0 ? '16px' : '8px 16px 12px',
          display: 'flex',
          flexDirection: 'column',
          gap: 2,
          maxHeight: 180,
          overflowY: 'auto',
        }}
      >
        {steps.length === 0 ? (
          <div style={{ fontSize: 13, lineHeight: 1.5, color: '#8C877D' }}>
            {isPreview
              ? 'No agent steps yet — preview mode, not connected to live agents. Per-agent steps (research, draft, jury, route) will stream here once the studio backend is wired.'
              : 'No agent steps yet. Steps will appear here as the team runs.'}
          </div>
        ) : (
          steps.map((step) => {
            const s = STATUS_STYLE[step.status];
            return (
              <div
                key={step.id}
                role="listitem"
                style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '6px 0' }}
              >
                <span
                  aria-hidden
                  style={{ color: s.color, fontSize: 13, lineHeight: '20px', flex: '0 0 auto' }}
                >
                  {s.glyph}
                </span>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                    <span style={{ fontSize: 13, fontWeight: 600, color: '#2A2722' }}>
                      {step.label}
                    </span>
                    <span style={{ fontSize: 11, color: STUDIO_ROLE_COLOR[step.agent] }}>
                      {STUDIO_ROLE_LABEL[step.agent]}
                    </span>
                    <span style={{ fontSize: 11, color: s.color }}>{s.label}</span>
                  </div>
                  {step.detail && (
                    <span style={{ fontSize: 12, color: '#6B6461', lineHeight: 1.4 }}>
                      {step.detail}
                    </span>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>
    </section>
  );
}
