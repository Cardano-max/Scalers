'use client';

/**
 * Live fleet strip: every non-finished run with its activity state, straight
 * from the supervisor's patrol data (the SHARED /studio/fleet poll — one loop
 * for the whole console, see lib/studio/useFleet). Renders nothing when the
 * fleet is idle — the runs list below already covers history. New rows animate
 * in (spring-in) so arriving work is visible, not a silent mutation.
 */
import { useFleet, activeFleetRows, type FleetRow } from '@/lib/studio/useFleet';

const ACTIVITY_COLOR: Record<FleetRow['activity'], string> = {
  working: '#157F4B',
  starting: '#0B6F68',
  stalled: '#B45309',
  'waiting-operator': '#B45309',
  done: '#A8A299',
  failed: '#B42318',
};

/** Plain-language activity labels — the raw state stays in the tooltip. */
const ACTIVITY_LABEL: Record<FleetRow['activity'], string> = {
  working: 'working',
  starting: 'starting up',
  stalled: 'stuck — may need a look',
  'waiting-operator': 'waiting for you',
  done: 'done',
  failed: 'failed',
};

export function FleetBoard({ onOpenRun }: { onOpenRun?: (runId: string) => void }) {
  const fleet = useFleet();
  const rows = activeFleetRows(fleet.rows);

  if (fleet.error || rows.length === 0) return null;

  return (
    <div style={{ margin: '0 4px 14px', border: '1px solid var(--border, #E5E1D8)', borderRadius: 9, padding: '10px 12px' }}>
      <div style={{ fontSize: 10, fontFamily: "'IBM Plex Mono', monospace", color: '#A8A299', letterSpacing: '0.7px', paddingBottom: 8 }}>
        AGENTS AT WORK — LIVE
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {rows.map((r) => (
          <button
            key={r.run_id}
            type="button"
            className="spring-in"
            onClick={() => onOpenRun?.(r.run_id)}
            title={`activity: ${r.activity}`}
            style={{
              display: 'flex',
              gap: 10,
              alignItems: 'center',
              fontSize: 12,
              fontFamily: "'IBM Plex Mono', monospace",
              background: 'transparent',
              border: 'none',
              cursor: onOpenRun ? 'pointer' : 'default',
              textAlign: 'left',
              padding: 0,
              minWidth: 0,
            }}
          >
            <span style={{ color: ACTIVITY_COLOR[r.activity], fontWeight: 600, minWidth: 128 }}>
              ● {ACTIVITY_LABEL[r.activity]}
            </span>
            <span style={{ color: 'var(--text, #38342C)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {r.run_id}
            </span>
            <span style={{ color: '#A8A299', whiteSpace: 'nowrap' }}>
              {r.last_role ?? '—'}
              {r.last_step_age_s != null ? ` · ${Math.round(r.last_step_age_s)}s ago` : ''}
              {` · ${r.n_steps} steps`}
              {r.n_pending_drafts > 0 ? ` · ${r.n_pending_drafts} drafts held` : ''}
              {r.n_pending_directives > 0 ? ` · ${r.n_pending_directives} directive(s) queued` : ''}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
