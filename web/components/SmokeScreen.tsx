'use client';

/**
 * Smoke screen — proves the data spine end to end on the FE foundation: it
 * reads a kkg.4 query (overview) through the active adapter, renders typed
 * models with loading/empty/error states, AND subscribes to the SSE stream,
 * appending live `feed.event`s with the connection status. When kkg.4 ships,
 * the same bindings light up against the live gateway with no code change.
 *
 * The richer Overview/Activity/etc. screens replace this per their own beads
 * (45v.8, 45v.4, …); this screen exists so the foundation is verifiably wired.
 */
import { useEffect, useRef, useState } from 'react';
import { useData } from '@/lib/data/DataProvider';
import { useAsync, isEmpty } from '@/lib/useAsync';
import { AsyncBoundary } from './states';
import { Dot } from './icons';
import { FeedRow } from './FeedRow';
import { CHANNEL_COLOR } from '@/lib/tokens';
import type { FeedEvent, Overview } from '@/lib/data/models';
import type { SSEStatus } from '@/lib/data/sse';

export function SmokeScreen() {
  const { adapter, tenantId } = useData();
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

  return (
    <div style={{ padding: 'var(--pad-section)', display: 'grid', gap: 20, maxWidth: 1400, marginInline: 'auto' }}>
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

      {/* live SSE feed */}
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
        {live.length === 0 ? (
          <div style={{ padding: 16, color: 'var(--text-muted)', fontSize: 13 }}>
            Waiting for events…
          </div>
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {live.map((e) => (
              <li key={e.id} className="enter" style={{ padding: 0, borderBottom: 'none' }}>
                <FeedRow event={e} />
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* prove channel token map renders from typed enums */}
      <div style={{ display: 'flex', gap: 14, alignItems: 'center', fontSize: 12, color: 'var(--text-muted)' }}>
        {(Object.keys(CHANNEL_COLOR) as Array<keyof typeof CHANNEL_COLOR>).map((c) => (
          <span key={c} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <Dot color={CHANNEL_COLOR[c]} /> {c}
          </span>
        ))}
      </div>
    </div>
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
