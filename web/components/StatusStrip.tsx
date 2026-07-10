'use client';

/**
 * What's-happening strip — a slim, always-visible status line under the TopBar:
 * "● 2 agents working · 13 drafts waiting for you · all else quiet". Built ONLY
 * from data the console already polls/fetches (the shared /studio/fleet poll +
 * the review-queue count the shell already loads) — no new poll loops. Clicking
 * it opens the Runs screen. When idle it says so honestly, with the time of the
 * last recorded activity when one is known.
 */
import { useConsole } from '@/state/console-store';
import { useFleet, activeFleetRows } from '@/lib/studio/useFleet';
import { clockTime } from './console-bits';

export function StatusStrip({ reviewCount }: { reviewCount: number }) {
  const { navigate } = useConsole();
  const fleet = useFleet();

  const active = activeFleetRows(fleet.rows);
  const nAgents = active.length;

  const parts: string[] = [];
  if (nAgents > 0) parts.push(`${nAgents} agent${nAgents === 1 ? '' : 's'} working`);
  if (reviewCount > 0)
    parts.push(`${reviewCount} draft${reviewCount === 1 ? '' : 's'} waiting for you`);

  const busy = parts.length > 0;

  // Honest "last activity": the freshest step across the fleet (age in seconds
  // relative to when we fetched). Omitted when nothing is known — never faked.
  let lastActivity: string | null = null;
  if (!busy && fleet.fetchedAt !== null) {
    const ages = fleet.rows
      .map((r) => r.last_step_age_s)
      .filter((a): a is number => typeof a === 'number' && Number.isFinite(a));
    if (ages.length > 0) {
      const freshest = Math.min(...ages);
      lastActivity = clockTime(new Date(fleet.fetchedAt - freshest * 1000).toISOString());
    }
  }

  const label = busy
    ? `${parts.join(' · ')} · all else quiet`
    : lastActivity
      ? `All quiet — last activity ${lastActivity}`
      : 'All quiet';

  return (
    <button
      type="button"
      aria-label={`System status: ${label}. Open runs.`}
      title="See what the team is doing"
      onClick={() => navigate('runs')}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        width: '100%',
        textAlign: 'left',
        font: 'inherit',
        fontFamily: 'var(--font-mono)',
        fontSize: 11,
        color: busy ? 'var(--accent-dark)' : 'var(--text-muted)',
        background: 'var(--surface)',
        border: 'none',
        borderBottom: '1px solid var(--hairline)',
        padding: '5px 24px',
        cursor: 'pointer',
      }}
    >
      <span
        aria-hidden
        className={busy ? 'active-pulse' : undefined}
        style={{
          width: 7,
          height: 7,
          borderRadius: '50%',
          background: busy ? 'var(--accent)' : 'var(--text-faint)',
          flex: '0 0 auto',
        }}
      />
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {label}
      </span>
      <span aria-hidden style={{ marginLeft: 'auto', color: 'var(--text-faint)', flex: '0 0 auto' }}>
        →
      </span>
    </button>
  );
}
