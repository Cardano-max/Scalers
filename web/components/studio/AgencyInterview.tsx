'use client';

/**
 * AgencyInterview — the scoping-interview GATE that replaces the Agency page's old
 * bare "Run campaign" button (P1a). The supervisor asks for the campaign's goal,
 * audience, channels, type, and output count (then optional refinements); the Run
 * button stays LOCKED until the engine reports the plan is armed. Only then can the
 * operator start the held run. Nothing here sends — arming merely UNLOCKS the
 * existing HELD /studio/run path.
 *
 * Presentational + props-driven: the container (AgencyScreen) owns the real
 * POST /studio/interview round-trip and passes the authoritative gate state in.
 */
import { useEffect, useState } from 'react';
import { ALL_META, type FieldMeta, type InterviewState } from '@/lib/studio/interview';

const TEAL = '#0F8A82';

function metaFor(field: string | undefined): FieldMeta | undefined {
  return ALL_META.find((m) => m.field === field);
}

function chipValue(field: string, value: unknown): string {
  if (Array.isArray(value)) return value.join(', ');
  if (typeof value === 'boolean') {
    if (field === 'drafts_only') return value ? 'drafts only' : 'stage for approval';
    return value ? 'yes' : 'no';
  }
  if (value == null || value === '' || value === 0) return '';
  return String(value);
}

export function AgencyInterview({
  state,
  busy,
  connected,
  running,
  onAnswer,
  onRun,
}: {
  state: InterviewState | null;
  busy: boolean;
  connected: boolean;
  running: boolean;
  onAnswer: (field: string, value: string) => void;
  onRun: () => void;
}) {
  const next = state?.nextQuestion ?? null;
  const meta = metaFor(next?.field);
  const [draft, setDraft] = useState('');

  // Reset the input when the question changes (a new field is being asked).
  useEffect(() => {
    setDraft('');
  }, [next?.field]);

  if (!connected) {
    return (
      <div style={panelStyle}>
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-muted)', textAlign: 'center' }}>
          Backend unreachable — this is the honest not-connected state. The interview and run
          cannot start until the studio endpoint responds.
        </p>
      </div>
    );
  }

  const armed = !!state?.armed;

  const submitText = () => {
    const v = draft.trim();
    if (!v || !next) return;
    onAnswer(next.field, v);
  };

  return (
    <section aria-label="Campaign scoping interview" style={panelStyle}>
      <header style={{ display: 'flex', flexDirection: 'column', gap: 4, textAlign: 'center', alignItems: 'center' }}>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600, letterSpacing: '-0.01em', color: 'var(--ink)' }}>
          Let's scope your campaign
        </h2>
        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.5, color: 'var(--text-secondary)', maxWidth: 460 }}>
          The supervisor gathers what it needs before the team starts — it never runs blindly.
          Answer by text here, or use the Voice tab. Nothing is sent; every draft is held for your approval.
        </p>
      </header>

      {/* Collected-so-far chips — real answers only, ✓ when present. */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, justifyContent: 'center' }}>
        {ALL_META.map((m) => {
          const val = chipValue(m.field, state?.collected?.[m.field]);
          const gating = (state?.gatingFields ?? []).includes(m.field);
          const done = !!val;
          return (
            <span
              key={m.field}
              title={m.question}
              style={{
                fontSize: 11.5,
                fontWeight: 540,
                color: done ? TEAL : 'var(--text-muted)',
                background: done ? 'rgba(15,138,130,0.08)' : 'var(--surface-alt)',
                border: `1px solid ${done ? 'rgba(15,138,130,0.3)' : 'var(--hairline)'}`,
                borderRadius: 'var(--radius-pill)',
                padding: '3px 9px',
                maxWidth: 240,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {done ? '✓ ' : gating ? '• ' : ''}
              {m.label}
              {done ? `: ${val}` : gating ? '' : ' (optional)'}
            </span>
          );
        })}
      </div>

      {/* The current question + an input appropriate to its kind. */}
      {next && meta && (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 9,
            background: '#fff',
            border: '1px solid var(--hairline)',
            borderRadius: 'var(--radius-card)',
            padding: 14,
            boxShadow: 'var(--shadow-card)',
          }}
        >
          <label htmlFor="agency-interview-input" style={{ fontSize: 13.5, fontWeight: 560, color: 'var(--ink)' }}>
            {next.question}
          </label>

          {meta.kind === 'yesno' ? (
            <div style={{ display: 'flex', gap: 8 }}>
              <ChoiceButton label="Yes" onClick={() => onAnswer(next.field, 'yes')} disabled={busy} />
              <ChoiceButton label="No" onClick={() => onAnswer(next.field, 'no')} disabled={busy} />
            </div>
          ) : meta.kind === 'drafts_or_stage' ? (
            <div style={{ display: 'flex', gap: 8 }}>
              <ChoiceButton label="Drafts only" onClick={() => onAnswer(next.field, 'drafts')} disabled={busy} />
              <ChoiceButton label="Stage for approval" onClick={() => onAnswer(next.field, 'stage')} disabled={busy} />
            </div>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                submitText();
              }}
              style={{ display: 'flex', gap: 8 }}
            >
              <input
                id="agency-interview-input"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                inputMode={meta.kind === 'number' ? 'numeric' : 'text'}
                placeholder={meta.kind === 'list' ? 'comma-separated' : meta.kind === 'number' ? 'a number' : 'your answer'}
                disabled={busy}
                autoComplete="off"
                style={{
                  flex: 1,
                  fontSize: 13.5,
                  padding: '9px 11px',
                  borderRadius: 'var(--radius-button)',
                  border: '1px solid var(--hairline-strong)',
                  background: busy ? 'var(--surface-alt)' : '#fff',
                  color: 'var(--ink)',
                }}
              />
              <button
                type="submit"
                disabled={busy || !draft.trim()}
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  color: '#fff',
                  background: busy || !draft.trim() ? '#9DBDB9' : TEAL,
                  border: 'none',
                  borderRadius: 'var(--radius-button)',
                  padding: '9px 16px',
                  cursor: busy || !draft.trim() ? 'not-allowed' : 'pointer',
                }}
              >
                Next
              </button>
            </form>
          )}
        </div>
      )}

      {/* The gated Run control. */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'center' }}>
        {armed && state?.readyMessage && (
          <p style={{ margin: 0, fontSize: 12.5, color: TEAL, fontWeight: 560, textAlign: 'center', maxWidth: 420 }}>
            {state.readyMessage}
          </p>
        )}
        <button
          type="button"
          onClick={onRun}
          disabled={!armed || running || busy}
          aria-label="Run campaign"
          title={armed ? 'Start the held campaign run' : 'Answer the questions above to unlock'}
          style={{
            fontSize: 13.5,
            fontWeight: 600,
            color: '#fff',
            background: !armed || running || busy ? '#C4D6D4' : TEAL,
            border: 'none',
            padding: '11px 24px',
            borderRadius: 'var(--radius-button)',
            cursor: !armed || running || busy ? 'not-allowed' : 'pointer',
            boxShadow: armed ? 'var(--shadow-selected)' : 'none',
          }}
        >
          {running ? 'Starting…' : 'Run campaign'}
        </button>
        {!armed && (
          <span style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
            Answer the questions above to unlock the run.
          </span>
        )}
      </div>
    </section>
  );
}

function ChoiceButton({ label, onClick, disabled }: { label: string; onClick: () => void; disabled: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      style={{
        fontSize: 13,
        fontWeight: 560,
        color: TEAL,
        background: '#fff',
        border: `1px solid ${TEAL}`,
        borderRadius: 'var(--radius-button)',
        padding: '8px 16px',
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.6 : 1,
      }}
    >
      {label}
    </button>
  );
}

const panelStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 16,
  maxWidth: 560,
  margin: '0 auto',
  padding: 24,
};
