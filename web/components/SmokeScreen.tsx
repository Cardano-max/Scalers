'use client';

/**
 * Overview screen — the at-a-glance home for a tenant, bound to the REAL engine
 * via the active adapter. It reads the `overview` query (KPIs + recent campaign
 * runs + a feed preview, all derived from real runs/actions) AND subscribes to
 * the SSE stream so new feed events arrive live. Every number is real or an
 * honest empty/"no data yet" state — never a fabricated metric.
 *
 * On the live source the KPIs that have no source yet (posts published, outreach
 * today) show 0 honestly; the review-queue count + recent runs are real.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useData } from '@/lib/data/DataProvider';
import { IntelligencePanel } from './studio/IntelligencePanel';
import { useAsync, isEmpty } from '@/lib/useAsync';
import { useConsole } from '@/state/console-store';
import { AsyncBoundary } from './states';
import { Dot } from './icons';
import { FeedRow } from './FeedRow';
import { Chip, clockTime } from './console-bits';
import type { ChipTone } from './console-bits';
import type { FeedEvent, Overview, Run } from '@/lib/data/models';
import type { SSEStatus } from '@/lib/data/sse';

export function SmokeScreen() {
  const { adapter, tenantId } = useData();
  const console = useConsole();
  const overview = useAsync<Overview>(() => adapter.getOverview(tenantId), [tenantId]);

  const [live, setLive] = useState<FeedEvent[]>([]);
  const [status, setStatus] = useState<SSEStatus>('connecting');
  const subRef = useRef<ReturnType<typeof adapter.subscribe> | null>(null);

  useEffect(() => {
    const sub = adapter.subscribe(
      tenantId,
      {
        'feed.event': (e) => setLive((prev) => [e, ...prev].slice(0, 12)),
      },
      setStatus,
    );
    subRef.current = sub;
    return () => sub.close();
  }, [adapter, tenantId]);

  // The feed shows the REAL recent feed (overview.feedPreview) immediately, with
  // live SSE events prepended as they arrive — de-duped by id so a live event
  // that matches a preview row doesn't double up.
  const feedRows = useMemo(() => {
    const preview = overview.data?.feedPreview ?? [];
    const seen = new Set(live.map((e) => e.id));
    return [...live, ...preview.filter((e) => !seen.has(e.id))];
  }, [live, overview.data?.feedPreview]);

  return (
    <div style={{ padding: 'var(--pad-section)', display: 'grid', gap: 20, maxWidth: 1180, marginInline: 'auto' }}>
      {/* Executive brain: evidence-backed "run next" recommendations from real rows. */}
      <IntelligencePanel />
      {/* KPI strip from the overview query */}
      <AsyncBoundary
        loading={overview.loading}
        error={overview.error}
        data={overview.data}
        empty={isEmpty(overview.data)}
        onRetry={overview.reload}
        emptyTitle="Engine has no data yet"
        emptyHint="Once the harness produces actions, KPIs appear here."
      >
        {(ov) => (
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <Kpi label="Autonomy · today" value={`${Math.round(ov.kpis.autonomyPct * 100)}%`} accent="teal" />
            <Kpi label="Review queue" value={String(ov.kpis.reviewQueueCount)} accent="amber" />
            <Kpi label="Outreach · today" value={String(ov.kpis.outreachToday)} />
            <Kpi label="Comments handled" value={String(ov.kpis.commentsAuto + ov.kpis.commentsReview)} />
            <Kpi label="Posts published" value={String(ov.kpis.postsPublished)} />
          </div>
        )}
      </AsyncBoundary>

      {/* Recent campaign runs (real runs derived from the engine) */}
      <div
        style={{
          border: '1px solid var(--hairline)',
          borderRadius: 'var(--radius-card)',
          background: 'var(--surface)',
          boxShadow: 'var(--shadow-card)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 16px', borderBottom: '1px solid var(--hairline-light)' }}>
          <span style={{ fontWeight: 600, fontSize: 13 }}>Recent campaign runs</span>
          <span className="label" style={{ marginLeft: 'auto' }}>
            {overview.data ? `${overview.data.recentRuns.length} runs` : ''}
          </span>
        </div>
        {overview.data && overview.data.recentRuns.length > 0 ? (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {overview.data.recentRuns.map((r) => (
              <RunRow key={r.id} run={r} onOpen={() => console.navigate('runs', r.id)} />
            ))}
          </ul>
        ) : (
          <div style={{ padding: 16, color: 'var(--text-muted)', fontSize: 13 }}>
            No campaign runs yet — start one from the Voice or Agency tab.
          </div>
        )}
      </div>

      {/* live SSE feed seeded with the real feed preview */}
      <div
        style={{
          border: '1px solid var(--hairline)',
          borderRadius: 'var(--radius-card)',
          background: 'var(--surface)',
          boxShadow: 'var(--shadow-card)',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '12px 16px',
            borderBottom: '1px solid var(--hairline-light)',
          }}
        >
          <Dot color={status === 'open' ? 'var(--danger-dot)' : 'var(--amber-dot)'} live={status === 'open'} />
          <span style={{ fontWeight: 600, fontSize: 13 }}>Live feed</span>
          <span className="label" style={{ marginLeft: 'auto' }}>
            sse: {status}
          </span>
        </div>
        {feedRows.length === 0 ? (
          <div style={{ padding: 16, color: 'var(--text-muted)', fontSize: 13 }}>
            {overview.loading ? 'Loading recent activity…' : 'No activity yet.'}
          </div>
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: '0 16px' }}>
            {feedRows.map((e) => (
              <li key={e.id} className="enter" style={{ padding: 0, borderBottom: 'none' }}>
                <FeedRow event={e} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function RunRow({ run, onOpen }: { run: Run; onOpen: () => void }) {
  const statusTone: ChipTone =
    run.status === 'SUCCESS' ? 'success' : run.status === 'FAILED' ? 'danger' : 'neutral';
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        style={{
          width: '100%',
          textAlign: 'left',
          font: 'inherit',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '11px 16px',
          border: 'none',
          borderBottom: '1px solid var(--hairline-lighter)',
          background: 'transparent',
        }}
        onMouseEnter={(e) => {
          (e.currentTarget as HTMLElement).style.background = 'rgba(0,0,0,0.01)';
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLElement).style.background = 'transparent';
        }}
      >
        <span className="mono" style={{ fontSize: 12, color: '#0B6F68', flex: '0 0 auto' }}>{run.id}</span>
        <span style={{ fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{run.type}</span>
        <span style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
          {run.reviewCount} staged · {run.autoCount} auto
        </span>
        <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>{clockTime(run.startedAt)}</span>
          <Chip tone={statusTone}>{run.status}</Chip>
        </span>
      </button>
    </li>
  );
}

function Kpi({ label, value, accent }: { label: string; value: string; accent?: 'teal' | 'amber' }) {
  const bg = accent === 'amber' ? 'var(--amber-kpi-bg)' : accent === 'teal' ? 'var(--auto-chip-bg)' : 'var(--surface)';
  return (
    <div
      style={{
        flex: '1 1 150px',
        minWidth: 150,
        border: '1px solid var(--hairline)',
        borderRadius: 'var(--radius-card)',
        background: bg,
        padding: 'var(--pad-card)',
        boxShadow: 'var(--shadow-card)',
      }}
    >
      <div className="label">{label}</div>
      <div style={{ fontSize: 29, fontWeight: 600, letterSpacing: '-0.6px', marginTop: 4 }}>{value}</div>
    </div>
  );
}
