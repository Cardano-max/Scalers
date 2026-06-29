'use client';

/**
 * Harness status card (sidebar bottom). Shows running/paused state with the
 * master Pause/Resume control + the operator chip. Pause/Resume calls
 * setEngineState — this is the harness master control, NOT autonomy: it never
 * enables auto, and it is safe while the 439 HOLD is active.
 */
import { Dot } from './icons';
import type { EngineState } from '@/lib/data/models';

export function HarnessStatusCard({
  engineState,
  onToggle,
  operatorName,
}: {
  engineState: EngineState;
  onToggle: () => void;
  operatorName: string;
}) {
  const running = engineState === 'RUNNING';
  const initials = operatorName
    .split(' ')
    .map((p) => p[0])
    .slice(0, 2)
    .join('');

  return (
    <div style={{ padding: '12px 14px 16px' }}>
      <div
        style={{
          border: '1px solid var(--hairline)',
          borderRadius: 'var(--radius-card)',
          background: 'var(--surface-alt)',
          padding: 'var(--pad-card)',
          boxShadow: 'var(--shadow-card)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Dot color={running ? 'var(--success-dot)' : 'var(--amber-dot)'} live={running} />
            <span style={{ fontWeight: 600, fontSize: 13 }}>
              {running ? 'Harness running' : 'Harness paused'}
            </span>
          </div>
          <span className="label" style={{ fontSize: 9.5 }}>
            {running ? 'live' : 'held'}
          </span>
        </div>
        <button
          type="button"
          onClick={onToggle}
          style={{
            marginTop: 10,
            width: '100%',
            padding: '8px 12px',
            borderRadius: 'var(--radius-button)',
            border: '1px solid var(--hairline-strong)',
            background: 'var(--surface)',
            cursor: 'pointer',
            font: 'inherit',
            fontSize: 13,
            fontWeight: 500,
            color: 'var(--text-secondary-2)',
          }}
        >
          {running ? 'Pause harness' : 'Resume harness'}
        </button>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 14, padding: '0 2px' }}>
        <div
          style={{
            width: 28,
            height: 28,
            borderRadius: '50%',
            background: 'var(--reasoning-bg)',
            color: 'var(--reasoning-text)',
            display: 'grid',
            placeItems: 'center',
            fontSize: 11,
            fontWeight: 600,
          }}
        >
          {initials}
        </div>
        <div style={{ lineHeight: 1.2 }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>{operatorName}</div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>Operator · Scalers</div>
        </div>
      </div>
    </div>
  );
}
