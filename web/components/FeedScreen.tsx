'use client';

import { useEffect, useMemo, useState } from 'react';
import { useData } from '@/lib/data/DataProvider';
import { useAsync } from '@/lib/useAsync';
import { useConsole } from '@/state/console-store';
import { Dot } from './icons';
import { FeedRow } from './FeedRow';
import type { FeedEvent, Worker } from '@/lib/data/models';

const WORKERS: Array<{ id: Worker; label: string }> = [
  { id: 'OUTREACH', label: 'Outreach' },
  { id: 'RESPONDER', label: 'Responder' },
  { id: 'JURY', label: 'Jury' },
  { id: 'CLASSIFIER', label: 'Classifier' },
  { id: 'SAFETY', label: 'Safety' },
  { id: 'MAILBOX_MCP', label: 'Mailbox' },
  { id: 'META_MCP', label: 'Meta MCP' },
  { id: 'WEBHOOK', label: 'Webhook' },
];

export function FeedScreen() {
  const { adapter, tenantId } = useData();
  const console = useConsole();
  const feed = useAsync<FeedEvent[]>(() => adapter.getFeed(tenantId), [tenantId]);

  const [filter, setFilter] = useState<Worker | null>(null);
  const [paused, setPaused] = useState(false);

  const items = useMemo(() => feed.data ?? [], [feed.data]);
  const filtered = useMemo(() => {
    if (!filter) return items;
    return items.filter((f) => f.worker === filter);
  }, [items, filter]);

  return (
    <section
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '15px 28px',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          borderBottom: '1px solid var(--hairline)',
          background: 'var(--surface)',
          flexWrap: 'wrap',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginRight: 6 }}>
          <Dot color="#0F8A82" live />
          <span style={{ fontSize: 13, fontWeight: 600 }}>Live activity stream</span>
        </div>

        {/* Filter chips */}
        {WORKERS.map((w) => (
          <button
            key={w.id}
            type="button"
            onClick={() => setFilter(filter === w.id ? null : w.id)}
            style={{
              fontSize: 12.5,
              fontWeight: filter === w.id ? 600 : 500,
              color: filter === w.id ? '#0B6F68' : '#46423B',
              background: filter === w.id ? '#E1F1EF' : '#fff',
              border: `1px solid ${filter === w.id ? '#C9E5E1' : '#E0DCD3'}`,
              padding: '6px 12px',
              borderRadius: 8,
              cursor: 'pointer',
            }}
            onMouseEnter={(e) => {
              if (filter !== w.id) {
                (e.currentTarget as HTMLElement).style.background = '#F1EFEA';
              }
            }}
            onMouseLeave={(e) => {
              if (filter !== w.id) {
                (e.currentTarget as HTMLElement).style.background = '#fff';
              }
            }}
          >
            {w.label}
          </button>
        ))}

        <span style={{ flex: 1 }} />

        {/* Pause button */}
        <button
          type="button"
          onClick={() => setPaused(!paused)}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 7,
            background: '#fff',
            border: '1px solid #E0DCD3',
            color: '#46423B',
            fontSize: 12.5,
            fontWeight: 500,
            padding: '7px 13px',
            borderRadius: 8,
            cursor: 'pointer',
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLElement).style.background = '#F1EFEA';
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLElement).style.background = '#fff';
          }}
        >
          {paused ? '▶ Resume' : '⏸ Pause'}
        </button>
      </div>

      {/* Feed list */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '6px 28px 44px',
          minHeight: 0,
        }}
      >
        {filtered.map((event) => (
          <FeedRow key={event.id} event={event} />
        ))}
      </div>
    </section>
  );
}
