'use client';

/**
 * PlannedSteps — the dynamic step-selection display (P4). The engine no longer runs a
 * fixed pipeline: for each run it picks a mode and decides WHICH steps execute and
 * which are skipped, with a reason for each. This surfaces that plan so the operator
 * sees, before the run starts, exactly what the team will do and why.
 *
 * HONESTY: renders nothing when the engine sent no plan (absent/empty plannedSteps).
 * Selected steps show their reason + the tools they will use; skipped steps are shown
 * muted and struck through with the reason they were left out. Nothing is fabricated.
 */
import type { PlannedStep } from '@/lib/studio/interview';

const TEAL = '#0F8A82';

export function PlannedSteps({
  steps,
  modeLabel,
}: {
  steps?: PlannedStep[];
  modeLabel?: string;
}) {
  if (!steps || steps.length === 0) return null;

  return (
    <section
      aria-label="Planned steps"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        background: '#fff',
        border: '1px solid var(--hairline)',
        borderRadius: 'var(--radius-card)',
        padding: 14,
        boxShadow: 'var(--shadow-card)',
      }}
    >
      <h3 style={{ margin: 0, fontSize: 13.5, fontWeight: 600, color: 'var(--ink)' }}>
        Plan: {modeLabel ?? 'custom'}
      </h3>
      <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {steps.map((s) => (
          <li
            key={s.id}
            data-step-id={s.id}
            data-selected={s.selected ? 'true' : 'false'}
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
              borderLeft: `3px solid ${s.selected ? TEAL : 'var(--hairline-strong)'}`,
              borderRadius: 6,
              padding: '6px 10px',
              background: s.selected ? 'rgba(15,138,130,0.04)' : 'var(--surface-alt)',
              opacity: s.selected ? 1 : 0.7,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span
                aria-hidden
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  color: s.selected ? TEAL : 'var(--text-faint)',
                }}
              >
                {s.selected ? '●' : '○'}
              </span>
              <span
                style={{
                  fontSize: 13,
                  fontWeight: 560,
                  color: s.selected ? 'var(--ink)' : 'var(--text-muted)',
                  textDecoration: s.selected ? 'none' : 'line-through',
                }}
              >
                {s.label}
              </span>
              {!s.selected && (
                <span style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--text-faint)', letterSpacing: '0.02em' }}>
                  SKIPPED
                </span>
              )}
            </div>
            <div style={{ fontSize: 11.5, color: 'var(--text-muted)', lineHeight: 1.45 }}>
              {s.reason}
            </div>
            {s.selected && s.tools.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                {s.tools.map((t) => (
                  <span
                    key={t}
                    style={{
                      fontSize: 10.5,
                      fontFamily: 'var(--font-mono)',
                      color: TEAL,
                      background: 'rgba(15,138,130,0.08)',
                      border: '1px solid rgba(15,138,130,0.3)',
                      borderRadius: 'var(--radius-pill)',
                      padding: '1px 7px',
                    }}
                  >
                    {t}
                  </span>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
