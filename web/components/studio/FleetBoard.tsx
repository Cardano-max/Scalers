'use client';

/**
 * Live fleet strip: every non-finished run with its activity state, straight
 * from the supervisor's patrol data (the SHARED /studio/fleet poll — one loop
 * for the whole console, see lib/studio/useFleet). Renders nothing when the
 * fleet is idle — the runs list below already covers history. New rows animate
 * in (spring-in) so arriving work is visible, not a silent mutation.
 */
import { useState } from 'react';
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

/** The brake. Stops this run and every channel leg of it (POST /studio/run/{id}/stop).
 *  Narrowing only — it never sends, publishes, or lifts a gate. */
function StopRunButton({ runId }: { runId: string }) {
  const [state, setState] = useState<'idle' | 'stopping' | 'stopped' | 'failed'>('idle');

  const stop = async () => {
    if (state === 'stopping' || state === 'stopped') return;
    setState('stopping');
    try {
      const res = await fetch(`/studio/run/${encodeURIComponent(runId)}/stop`, { method: 'POST' });
      setState(res.ok ? 'stopped' : 'failed');
    } catch {
      setState('failed');
    }
  };

  return (
    <button
      type="button"
      onClick={stop}
      disabled={state === 'stopping' || state === 'stopped'}
      title={
        state === 'stopped'
          ? 'stopped — the run and all its legs were aborted'
          : 'Stop this run and every channel leg of it. Nothing is sent.'
      }
      style={{
        flexShrink: 0,
        fontSize: 10.5,
        fontFamily: "'IBM Plex Mono', monospace",
        fontWeight: 600,
        color: state === 'stopped' ? '#A8A299' : '#B42318',
        background: 'transparent',
        border: `1px solid ${state === 'stopped' ? '#E5E1D8' : '#B42318'}`,
        borderRadius: 999,
        padding: '2px 9px',
        cursor: state === 'stopped' ? 'default' : 'pointer',
      }}
    >
      {state === 'stopping' ? 'stopping…' : state === 'stopped' ? 'stopped' : state === 'failed' ? 'retry stop' : 'stop'}
    </button>
  );
}

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
          <div key={r.run_id} style={{ display: 'flex', gap: 8, alignItems: 'center', minWidth: 0 }}>
            <button
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
                flex: 1,
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
            {/* STOP. An operator watching a run they no longer want — or one that has gone
                stuck and will never reach a safe boundary on its own — had no way to end it:
                the console reported "2 agents working" indefinitely with no brake anywhere on
                the screen. This aborts the run AND every channel leg of it. It can only ever
                narrow: it sends nothing, publishes nothing, and lifts no gate. */}
            <StopRunButton runId={r.run_id} />
          </div>
        ))}
      </div>
    </div>
  );
}
