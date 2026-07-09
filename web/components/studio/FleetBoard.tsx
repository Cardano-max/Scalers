'use client';

import { useEffect, useState } from 'react';

/** One row of GET /studio/fleet — the supervisor's initech-style status board. */
type FleetRow = {
  run_id: string;
  status: string;
  activity: 'working' | 'starting' | 'stalled' | 'waiting-operator' | 'done' | 'failed';
  last_role: string | null;
  last_step_age_s: number | null;
  n_steps: number;
  n_pending_drafts: number;
  n_pending_directives: number;
  n_applied_directives: number;
};

const ACTIVITY_COLOR: Record<FleetRow['activity'], string> = {
  working: '#157F4B',
  starting: '#0B6F68',
  stalled: '#B45309',
  'waiting-operator': '#B45309',
  done: '#A8A299',
  failed: '#B42318',
};

/** Live fleet strip: every non-finished run with its activity state, straight
 * from the supervisor's patrol data. Renders nothing when the fleet is idle —
 * the runs list below already covers history. */
export function FleetBoard({ onOpenRun }: { onOpenRun?: (runId: string) => void }) {
  const [rows, setRows] = useState<FleetRow[]>([]);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      fetch('/studio/fleet')
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
        .then((d) => {
          if (alive) {
            setRows((d.fleet ?? []).filter((r: FleetRow) => r.activity !== 'done' && r.activity !== 'failed'));
            setError(false);
          }
        })
        .catch(() => alive && setError(true));
    load();
    const t = setInterval(load, 15000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  if (error || rows.length === 0) return null;

  return (
    <div style={{ margin: '0 4px 14px', border: '1px solid var(--border, #E5E1D8)', borderRadius: 9, padding: '10px 12px' }}>
      <div style={{ fontSize: 10, fontFamily: "'IBM Plex Mono', monospace", color: '#A8A299', letterSpacing: '0.7px', paddingBottom: 8 }}>
        AGENT FLEET — LIVE
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {rows.map((r) => (
          <button
            key={r.run_id}
            type="button"
            onClick={() => onOpenRun?.(r.run_id)}
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
              ● {r.activity}
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
