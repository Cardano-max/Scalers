'use client';

/**
 * CompetitorPickModal — the operator pick for a run paused on a COMPETITOR post
 * (kind 'competitor_pick', the competitor-research counterpart of the artwork
 * pause). Renders the engine's REAL question and its REAL scraped competitor
 * options (@handle, verbatim caption, real metrics, score, the "why it worked"
 * line) as cards. Clicking an option POSTs select-competitor (via the onSelect
 * callback) and the run resumes server-side — polling continues.
 *
 * HONESTY: only the engine-provided options render; there is no local fallback
 * competitor data and missing metrics/score simply do not render. "Decide later"
 * merely hides the dialog — the run STAYS paused and the inline paused banner
 * remains visible until a real pick is made.
 */
import { useState } from 'react';
import type { CompetitorSelectionRequest, CompetitorOption } from '@/lib/studio/run-trace';
import { Chip } from '../console-bits';

const TEAL = '#0F8A82';

export function CompetitorPickModal({
  request,
  busy = false,
  onSelect,
  onDismiss,
}: {
  request: CompetitorSelectionRequest;
  /** True while the select-competitor POST is in flight (disables the grid). */
  busy?: boolean;
  onSelect: (optionId: string) => void;
  /** Hide the dialog without resolving — the run stays paused. */
  onDismiss?: () => void;
}) {
  const [picked, setPicked] = useState<string | null>(null);

  const pick = (optionId: string) => {
    if (busy || picked) return;
    setPicked(optionId);
    onSelect(optionId);
  };

  return (
    <div
      role="presentation"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 60,
        background: 'rgba(20, 18, 14, 0.45)',
        display: 'grid',
        placeItems: 'center',
        padding: 20,
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onDismiss?.();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Pick a competitor post"
        className="spring-in"
        style={{
          width: 'min(720px, 100%)',
          maxHeight: '86vh',
          overflowY: 'auto',
          background: 'var(--surface, #fff)',
          border: '1px solid var(--hairline)',
          borderRadius: 'var(--radius-card)',
          boxShadow: '0 18px 48px rgba(0,0,0,0.22)',
          padding: 20,
          display: 'grid',
          gap: 14,
        }}
      >
        <header style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          <span
            aria-hidden
            className="active-pulse"
            style={{
              width: 10,
              height: 10,
              borderRadius: '50%',
              background: 'var(--amber-dot, #B58A2A)',
              marginTop: 6,
              flex: '0 0 auto',
              // @ts-expect-error custom prop for the pulse keyframe color
              '--pulse-color': 'rgba(181,138,42,0.5)',
            }}
          />
          <div style={{ minWidth: 0, flex: 1 }}>
            <h2 style={{ margin: 0, fontSize: 16.5, fontWeight: 650, letterSpacing: '-0.01em', color: 'var(--ink)' }}>
              The run is paused — pick the competitor post to mold
            </h2>
            <p style={{ margin: '4px 0 0', fontSize: 13, lineHeight: 1.5, color: 'var(--text-secondary)' }}>
              {request.question}
            </p>
          </div>
          {onDismiss && (
            <button
              type="button"
              onClick={onDismiss}
              aria-label="Decide later"
              title="Hide this dialog — the run stays paused until you pick"
              style={{
                font: 'inherit',
                fontSize: 12,
                color: 'var(--text-muted)',
                background: 'transparent',
                border: '1px solid var(--hairline)',
                borderRadius: 'var(--radius-button)',
                padding: '5px 10px',
                cursor: 'pointer',
                flex: '0 0 auto',
              }}
            >
              Decide later
            </button>
          )}
        </header>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
            gap: 12,
          }}
        >
          {request.options.map((opt) => (
            <CompetitorPostOption
              key={opt.id}
              option={opt}
              disabled={busy || (picked !== null && picked !== opt.id)}
              selecting={picked === opt.id}
              onPick={() => pick(opt.id)}
            />
          ))}
        </div>

        <p style={{ margin: 0, fontSize: 11.5, color: 'var(--text-muted)' }}>
          Clicking an option resumes the run molding that post&apos;s structure to your
          brand. Nothing is posted or sent — every draft still lands in the Review queue
          for your approval.
        </p>
      </div>
    </div>
  );
}

function CompetitorPostOption({
  option,
  disabled,
  selecting,
  onPick,
}: {
  option: CompetitorOption;
  disabled: boolean;
  selecting: boolean;
  onPick: () => void;
}) {
  // Real engagement numbers only — a metric the engine did not report never renders.
  const metricBits: string[] = [];
  if (typeof option.metrics.likes === 'number') {
    metricBits.push(`${option.metrics.likes.toLocaleString()} likes`);
  }
  if (typeof option.metrics.comments === 'number') {
    metricBits.push(`${option.metrics.comments.toLocaleString()} comments`);
  }
  return (
    <button
      type="button"
      onClick={onPick}
      disabled={disabled}
      aria-label={`Pick competitor post ${option.id}`}
      style={{
        font: 'inherit',
        textAlign: 'left',
        border: `2px solid ${selecting ? TEAL : 'var(--hairline)'}`,
        borderRadius: 12,
        overflow: 'hidden',
        background: '#fff',
        cursor: disabled ? 'default' : 'pointer',
        opacity: disabled && !selecting ? 0.55 : 1,
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
        padding: '10px 12px',
      }}
      onMouseEnter={(e) => {
        if (!disabled) (e.currentTarget as HTMLElement).style.borderColor = TEAL;
      }}
      onMouseLeave={(e) => {
        if (!selecting) (e.currentTarget as HTMLElement).style.borderColor = 'var(--hairline)';
      }}
    >
      <span style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        {option.handle && (
          <span style={{ fontSize: 12.5, fontWeight: 640, color: 'var(--ink)' }}>
            @{option.handle.replace(/^@/, '')}
          </span>
        )}
        {option.totalScore != null && (
          <span
            title="total score"
            style={{ fontSize: 11, fontWeight: 600, color: TEAL, fontVariantNumeric: 'tabular-nums' }}
          >
            score {option.totalScore}
          </span>
        )}
      </span>
      {option.caption && (
        <span
          style={{
            fontSize: 12,
            lineHeight: 1.45,
            color: 'var(--text-secondary)',
            display: '-webkit-box',
            WebkitLineClamp: 3,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}
        >
          {/* Verbatim caption — truncated by clamp, never paraphrased. */}
          {option.caption}
        </span>
      )}
      {metricBits.length > 0 && (
        <span style={{ fontSize: 11, color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>
          {metricBits.join(' · ')}
        </span>
      )}
      {option.visualTags.length > 0 && (
        <span style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {option.visualTags.map((t) => (
            <Chip key={`t_${t}`} tone="neutral" style={{ fontSize: 10 }}>
              {t}
            </Chip>
          ))}
        </span>
      )}
      {option.whyItWorked && (
        <span style={{ fontSize: 11.5, lineHeight: 1.45, color: 'var(--text-secondary)' }}>
          {option.whyItWorked}
        </span>
      )}
      <span style={{ fontSize: 11.5, fontWeight: 600, color: selecting ? TEAL : 'var(--text-muted)' }}>
        {selecting ? 'Resuming with this post…' : 'Mold this post →'}
      </span>
    </button>
  );
}
