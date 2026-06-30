'use client';

/**
 * ConfidenceEvidence — a clean, human-readable explanation of WHY a draft scored
 * the confidence it did, and why it routed to review / approve / reject. Built
 * ENTIRELY from real fields already on the Action:
 *   - confidence vs threshold + escalation.label  → the headline verdict
 *   - jury.dimensions (Brand voice / Safety / Appropriateness) → per-signal means
 *   - jury.agreement                              → juror consensus
 *   - judges[] (name, score, vote, reasoning)     → what each critic/jury said
 *   - gates[]                                     → deterministic blocks
 *
 * NOT raw JSON. HONEST: when a breakdown was not captured (no dimensions, no
 * judges), it says so plainly rather than rendering an empty/fabricated panel.
 * The "View full reasoning" button deep-links to the exact step trace.
 */
import { useConsole } from '@/state/console-store';
import type { Action } from '@/lib/data/models';

function pct(n: number): number {
  return Math.round(Math.max(0, Math.min(100, n * 100)));
}
function fmt(n: number): string {
  return n.toFixed(2);
}

export function ConfidenceEvidence({ action }: { action: Action }) {
  const console = useConsole();
  const { confidence, threshold, jury, gates, escalation } = action;
  const cleared = confidence >= threshold;
  const dims = jury?.dimensions ?? [];
  const judges = action.judges ?? [];
  const failingGates = gates.filter((g) => !g.ok);
  const failingDims = dims.filter((d) => d.verdict !== 'pass');
  const passJudges = judges.filter((j) => j.vote === 'pass').length;

  // Plain-language reason the item is where it is.
  const verdictLine = cleared
    ? `Confidence ${pct(confidence)}% cleared the ${pct(threshold)}% threshold.`
    : `Confidence ${pct(confidence)}% is below the ${pct(threshold)}% threshold — held for review.`;

  const driver =
    failingGates.length > 0
      ? `Blocked by a deterministic gate: ${failingGates.map((g) => g.label).join(', ')}.`
      : failingDims.length > 0
        ? `Pulled down by ${failingDims.map((d) => d.label.toLowerCase()).join(' + ')}.`
        : escalation?.label
          ? `Routed as: ${escalation.label}.`
          : null;

  return (
    <div
      style={{
        border: '1px solid var(--reasoning-border)',
        borderRadius: 'var(--radius-card)',
        background: 'var(--reasoning-bg)',
        padding: 'var(--pad-card)',
        display: 'grid',
        gap: 14,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span className="label" style={{ color: 'var(--reasoning-text)' }}>
          Why this confidence
        </span>
        <span
          className="mono"
          style={{
            marginLeft: 'auto',
            fontSize: 11,
            fontWeight: 700,
            color: cleared ? '#157F4B' : '#9A6B00',
            background: cleared ? '#E6F4EC' : '#FBF0D9',
            padding: '2px 9px',
            borderRadius: 5,
          }}
        >
          {cleared ? '✓ cleared' : '↑ review'}
        </span>
      </div>

      {/* Headline verdict + driver — plain language, not JSON. */}
      <div style={{ display: 'grid', gap: 4 }}>
        <span style={{ fontSize: 13.5, lineHeight: 1.5, color: 'var(--reasoning-text)' }}>{verdictLine}</span>
        {driver ? (
          <span style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--text-secondary)' }}>{driver}</span>
        ) : null}
      </div>

      {/* What the critics/jury said — the human-readable juror voices that explain
          the number. The per-DIMENSION breakdown lives in the Autonomy decision
          card above; we don't repeat it here. */}
      {judges.length > 0 ? (
        <div style={{ display: 'grid', gap: 7 }}>
          <span className="label" style={{ fontSize: 9.5 }}>
            Jury · {passJudges}/{judges.length} pass{jury?.agreement ? ` · ${jury.agreement}` : ''}
          </span>
          {judges.map((j) => (
            <div key={j.name} style={{ display: 'grid', gap: 2 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary-2)', flex: 1 }}>
                  {j.name}
                </span>
                <span
                  style={{
                    fontSize: 10.5,
                    fontWeight: 700,
                    color: j.vote === 'pass' ? '#157F4B' : '#B42318',
                  }}
                >
                  {j.vote === 'pass' ? '✓' : '✗'}
                </span>
                <span className="mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>{fmt(j.score)}</span>
              </div>
              {j.reasoning ? (
                <span style={{ fontSize: 12, lineHeight: 1.5, color: 'var(--text-secondary)' }}>{j.reasoning}</span>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <span style={{ fontSize: 12.5, color: 'var(--text-muted)', lineHeight: 1.5 }}>
          Per-juror rationale was not captured for this draft — the pooled confidence, threshold,
          and the per-dimension jury breakdown above are what is recorded. Nothing was fabricated.
        </span>
      )}

      {/* Link to the exact reasoning trace for this draft. */}
      <button
        type="button"
        onClick={() => console.navigate('step_detail', action.id)}
        style={{
          justifySelf: 'start',
          font: 'inherit',
          fontSize: 12.5,
          fontWeight: 600,
          color: 'var(--accent-dark)',
          background: '#fff',
          border: '1px solid var(--reasoning-border)',
          borderRadius: 'var(--radius-button)',
          padding: '7px 12px',
          cursor: 'pointer',
        }}
      >
        View full reasoning →
      </button>
    </div>
  );
}
