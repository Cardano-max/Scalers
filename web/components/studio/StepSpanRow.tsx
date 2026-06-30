'use client';

/**
 * StepSpanRow — one REAL RunStep (a row of agent_runs) as an expandable span:
 *   persona left-rail · role + mono model badge · duration · one-line evidence,
 * expandable to the full real input/output. Duration is a real createdAt delta
 * vs the previous step. Nothing is fabricated; an absent output shows a shimmer
 * skeleton (the step is active but has not returned) or an honest "no output".
 */
import { useState } from 'react';
import type { RunStep } from '@/lib/studio/run-trace';
import { personaForRunRole, stepSummaryLine, durationBetween } from '@/lib/studio/agency';

function pretty(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function StepSpanRow({
  step,
  prevCreatedAt,
  index,
  active = false,
}: {
  step: RunStep;
  prevCreatedAt?: string | null;
  index: number;
  /** True when this is the in-flight step with no output yet (shimmer skeleton). */
  active?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const persona = personaForRunRole(step.role);
  const dur = durationBetween(prevCreatedAt ?? null, step.createdAt ?? null);
  const summary = stepSummaryLine(step);
  const hasOutput = step.output != null && step.output !== '';

  return (
    <div
      className="spring-in"
      style={{
        display: 'flex',
        gap: 10,
        padding: '9px 11px',
        borderRadius: 9,
        background: '#fff',
        border: `1px solid var(--hairline)`,
        boxShadow: 'var(--shadow-card)',
        animationDelay: `${Math.min(index, 8) * 24}ms`,
      }}
    >
      <span
        aria-hidden
        style={{ width: 3, alignSelf: 'stretch', borderRadius: 3, background: persona.accent, flex: '0 0 auto' }}
      />
      <div style={{ minWidth: 0, flex: 1, display: 'flex', flexDirection: 'column', gap: 4 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 13, fontWeight: 590, color: persona.accent }}>{persona.name}</span>
          <span
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 11,
              color: 'var(--text-faint)',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            #{step.seq}
          </span>
          {step.model && (
            <span
              title="model"
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 10.5,
                color: 'var(--text-muted)',
                background: 'var(--surface-alt)',
                border: '1px solid var(--hairline)',
                borderRadius: 'var(--radius-chip)',
                padding: '1px 6px',
              }}
            >
              {step.model}
            </span>
          )}
          <span style={{ flex: 1 }} />
          {dur && (
            <span
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 11,
                color: 'var(--text-muted)',
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {dur}
            </span>
          )}
        </div>

        {active && !hasOutput ? (
          <div className="shimmer" style={{ height: 13, borderRadius: 5, width: '72%' }} />
        ) : summary ? (
          <div style={{ fontSize: 12.5, lineHeight: 1.45, color: 'var(--text-secondary)' }}>
            {open ? '' : summary.length > 160 ? `${summary.slice(0, 160)}…` : summary}
          </div>
        ) : (
          <div style={{ fontSize: 12, color: 'var(--text-faint)' }}>No output recorded for this step.</div>
        )}

        {hasOutput && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            style={{
              alignSelf: 'flex-start',
              fontSize: 11.5,
              fontWeight: 590,
              color: persona.accent,
              background: 'transparent',
              border: 'none',
              padding: 0,
              cursor: 'pointer',
            }}
          >
            {open ? 'Hide detail ▲' : 'Show detail ▾'}
          </button>
        )}

        {open && hasOutput && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 2 }}>
            {step.input != null && step.input !== '' && (
              <Pre label="input" text={pretty(step.input)} />
            )}
            <Pre label="output" text={pretty(step.output)} />
          </div>
        )}
      </div>
    </div>
  );
}

function Pre({ label, text }: { label: string; text: string }) {
  return (
    <div>
      <div className="label" style={{ marginBottom: 3 }}>
        {label}
      </div>
      <pre
        style={{
          margin: 0,
          fontFamily: 'var(--font-mono)',
          fontSize: 11,
          lineHeight: 1.5,
          color: 'var(--text-secondary)',
          background: 'var(--surface-alt)',
          border: '1px solid var(--hairline)',
          borderRadius: 7,
          padding: '8px 10px',
          maxHeight: 220,
          overflow: 'auto',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {text}
      </pre>
    </div>
  );
}
