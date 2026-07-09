'use client';

/**
 * RunNarration — the supervisor's LIVE team narration while a run executes (#11). As
 * each real agent_run lands, the host narrates it in plain language ("Researching Mia
 * — pulling their history…", "The copywriter is drafting a personalized email for…",
 * "The critic reviewed the draft — verdict: ship."). One line per REAL recorded step.
 *
 * HONESTY: every line is derived from the run's actual steps (engine run_narration,
 * mirrored client-side). Nothing is narrated that did not run; a failed step is shown
 * as a snag, not as success. Renders nothing until the first step lands.
 */
import { runNarration, type NarrationLine } from '@/lib/studio/narration';
import type { RunState } from '@/lib/studio/run-trace';

const TEAL = '#0F8A82';

export function RunNarration({ runState, running }: { runState: RunState | null; running: boolean }) {
  // Prefer the engine-derived narration; fall back to the client mirror over the steps.
  const lines: NarrationLine[] =
    runState?.narration && runState.narration.length > 0 ? runState.narration : runNarration(runState?.steps);

  if (lines.length === 0) return null;

  return (
    <section
      aria-label="Live team narration"
      data-testid="run-narration"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
        background: '#fff',
        border: '1px solid var(--hairline)',
        borderRadius: 'var(--radius-card)',
        boxShadow: 'var(--shadow-card)',
        padding: 14,
        marginBottom: 16,
      }}
    >
      <header style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 640, color: 'var(--ink)' }}>The team, live</span>
        {running && (
          <span style={{ fontSize: 11.5, fontWeight: 560, color: TEAL }} aria-hidden>
            ● working…
          </span>
        )}
      </header>
      <ol style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 4 }}>
        {lines.map((ln) => (
          <li
            key={`${ln.seq}-${ln.role}`}
            style={{
              fontSize: 12.5,
              lineHeight: 1.5,
              color: ln.failed ? '#9A3B2F' : 'var(--text-secondary)',
              display: 'flex',
              gap: 7,
            }}
          >
            <span aria-hidden style={{ color: ln.failed ? '#9A3B2F' : TEAL, fontWeight: 700 }}>
              {ln.failed ? '!' : '›'}
            </span>
            <span>{ln.line}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
