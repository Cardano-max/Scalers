'use client';

/**
 * JuryCard — per-dimension verdict summary + per-judge rationale inspector.
 *
 * Extracted verbatim from ActivityScreen.tsx:388-497 (card JSX) and
 * ActivityScreen.tsx:667-800 (JudgeInspectorModal function).
 *
 * Owns its own `selectedJudge` state so ActivityScreen and StepDetailScreen
 * can both mount this without lifting state.
 *
 * HONESTY RULES (spec §5):
 *   - judges[].reasoning is real only when autonomy_jury.judge_rationale is
 *     populated. A score-string pattern (e.g. "voice x · safety y · appr z")
 *     is rendered as-is — never authored into prose.
 *   - The [DEMO] badge shown for isSeeded rows is factual, not fabricated CoT.
 *
 * Props:
 *   jury      — JuryDecision with confidence + dimensions
 *   judges    — Judge[] (ActivityItem.judges); component renders null when empty
 *   isSeeded  — true → shows [DEMO] badge in the inspector modal
 */

import { useState } from 'react';
import type { Judge, JuryDecision } from '@/lib/data/models';

interface JuryCardProps {
  jury: JuryDecision;
  judges: Judge[];
  isSeeded: boolean;
}

export function JuryCard({ jury, judges, isSeeded }: JuryCardProps) {
  const [selectedJudge, setSelectedJudge] = useState<string | null>(null);

  if (!judges || judges.length === 0) {
    return null;
  }

  return (
    /* JURY card with per-dimension verdict summary */
    <div
      style={{
        border: '1px solid var(--hairline)',
        borderRadius: 'var(--radius-card)',
        background: 'var(--surface)',
        padding: 'var(--pad-card)',
        display: 'grid',
        gap: 14,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="label" style={{ color: 'var(--text-secondary)' }}>Jury · {judges.length} judges</span>
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 11, color: 'var(--teal)' }}>pooled {jury.confidence.toFixed(2)}</span>
      </div>

      {/* per-dimension verdict summary */}
      {jury.dimensions && jury.dimensions.length > 0 ? (
        <div style={{ display: 'grid', gap: 8 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.3px' }}>
            Dimension verdicts
          </div>
          <div style={{ display: 'grid', gap: 8 }}>
            {jury.dimensions.map((dim) => {
              const dimPassed = dim.verdict === 'pass';
              return (
                <div
                  key={dim.label}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    fontSize: 13,
                    color: 'var(--text-secondary)',
                  }}
                >
                  <span style={{ flex: 1, minWidth: 0 }}>{dim.label}</span>
                  <span
                    style={{
                      fontSize: 11,
                      fontWeight: 600,
                      color: dimPassed ? '#157F4B' : '#B42318',
                      background: dimPassed ? '#E6F4EC' : '#FBE9E6',
                      padding: '2px 8px',
                      borderRadius: 4,
                      flex: '0 0 auto',
                    }}
                  >
                    {dimPassed ? '✓ Pass' : '✗ Fail'}
                  </span>
                  <span className="mono" style={{ fontSize: 11, color: 'var(--text-secondary-2)', flex: '0 0 auto' }}>
                    ({dim.score.toFixed(2)})
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      {/* per-judge breakdown */}
      <div style={{ display: 'grid', gap: 10 }}>
        {judges.map((judge, i) => (
          <button
            key={i}
            type="button"
            onClick={() => setSelectedJudge(judge.name)}
            style={{
              all: 'unset',
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              cursor: 'pointer',
              padding: '6px 8px',
              borderRadius: 6,
              fontSize: 12.5,
            }}
          >
            <span style={{ fontSize: 12.5, fontWeight: 600, width: 80, flex: '0 0 auto' }}>{judge.name}</span>
            <span
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: judge.vote === 'pass' ? '#157F4B' : '#B42318',
                background: judge.vote === 'pass' ? '#E6F4EC' : '#FBE9E6',
                padding: '2px 8px',
                borderRadius: 5,
                flex: '0 0 auto',
              }}
            >
              {judge.vote === 'pass' ? '✓ Pass' : '✗ Fail'}
            </span>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)', flex: 1, minWidth: 0 }}>{judge.reasoning}</span>
            <span className="mono" style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary-2)', flex: '0 0 auto' }}>{judge.score.toFixed(2)}</span>
          </button>
        ))}
      </div>

      {/* Judge Inspector Modal */}
      {selectedJudge ? (
        <JudgeInspectorModal
          judge={judges.find((j) => j.name === selectedJudge) || null}
          isSeeded={isSeeded}
          onClose={() => setSelectedJudge(null)}
        />
      ) : null}
    </div>
  );
}

function JudgeInspectorModal({
  judge,
  isSeeded,
  onClose,
}: {
  judge: { name: string; score: number; vote: string; reasoning: string } | null;
  isSeeded: boolean;
  onClose: () => void;
}) {
  if (!judge) return null;

  return (
    <>
      {/* overlay */}
      <div
        role="presentation"
        onClick={onClose}
        style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          background: 'rgba(0, 0, 0, 0.4)',
          zIndex: 999,
        }}
      />
      {/* modal */}
      <div
        style={{
          position: 'fixed',
          top: '50%',
          left: '50%',
          transform: 'translate(-50%, -50%)',
          background: 'var(--surface)',
          border: '1px solid var(--hairline)',
          borderRadius: 'var(--radius-card)',
          boxShadow: 'var(--shadow-card)',
          padding: 'var(--pad-card)',
          maxWidth: 500,
          width: 'calc(100% - 32px)',
          maxHeight: '80vh',
          overflow: 'auto',
          zIndex: 1000,
          display: 'grid',
          gap: 16,
        }}
      >
        {/* header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 16, fontWeight: 600, flex: 1 }}>{judge.name}</span>
          {isSeeded && (
            <span
              style={{
                fontSize: 10,
                fontWeight: 700,
                color: '#5D5D5D',
                background: '#F0F0F0',
                padding: '3px 8px',
                borderRadius: 4,
                textTransform: 'uppercase',
                letterSpacing: '0.5px',
              }}
            >
              [DEMO]
            </span>
          )}
          <button
            type="button"
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              fontSize: 18,
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              padding: '0',
              display: 'grid',
              placeItems: 'center',
              width: 24,
              height: 24,
            }}
          >
            ✕
          </button>
        </div>

        {/* verdict badge */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: judge.vote === 'pass' ? '#157F4B' : '#B42318',
              background: judge.vote === 'pass' ? '#E6F4EC' : '#FBE9E6',
              padding: '4px 10px',
              borderRadius: 5,
            }}
          >
            {judge.vote === 'pass' ? '✓ Pass' : '✗ Fail'}
          </span>
          <span className="mono" style={{ fontSize: 12, color: 'var(--text-secondary)', fontWeight: 600 }}>
            {judge.score.toFixed(2)}
          </span>
        </div>

        {/* reasoning section */}
        <div style={{ display: 'grid', gap: 8 }}>
          <span className="label" style={{ fontSize: 10 }}>Reasoning</span>
          <div style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--ink)' }}>
            {judge.reasoning}
          </div>
        </div>

        {/* seeded demo data note */}
        {isSeeded && (
          <div
            style={{
              fontSize: 12,
              color: 'var(--text-secondary)',
              background: '#F9F9F9',
              border: '1px solid var(--hairline-light)',
              borderRadius: 'var(--radius-button)',
              padding: '10px 12px',
              marginTop: 4,
            }}
          >
            ⓘ Seeded demo data — not a live jury run
          </div>
        )}
      </div>
    </>
  );
}
