'use client';

import { useMemo, useState } from 'react';
import { useData } from '@/lib/data/DataProvider';
import { useAsync } from '@/lib/useAsync';
import { Dot } from './icons';
import { clockTime } from './console-bits';
import { WORKER_COLOR } from '@/lib/tokens';
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
          <div
            key={event.id}
            style={{
              display: 'flex',
              gap: 10,
              alignItems: 'flex-start',
              padding: '9px 2px',
              borderBottom: '1px solid #F2F0EA',
              animation: 'feedIn 0.3s ease-out',
            }}
          >
            <Dot color={WORKER_COLOR[event.worker] || '#8C877D'} />
            <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 3 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 9, flexWrap: 'wrap' }}>
                <span
                  style={{
                    fontSize: 11,
                    fontWeight: 600,
                    color: WORKER_COLOR[event.worker] || '#8C877D',
                    background: 'rgba(0,0,0,0.02)',
                    padding: '2px 6px',
                    borderRadius: 4,
                    textTransform: 'uppercase',
                  }}
                >
                  {event.worker}
                </span>
                {event.chip && (
                  <span
                    style={{
                      fontSize: 10,
                      fontWeight: 600,
                      color:
                        event.severity === 'SUCCESS'
                          ? '#157F4B'
                          : event.severity === 'ERROR'
                            ? '#B42318'
                            : event.severity === 'WARN'
                              ? '#9A6B00'
                              : '#8C877D',
                      background:
                        event.severity === 'SUCCESS'
                          ? '#E6F4EC'
                          : event.severity === 'ERROR'
                            ? '#FBE9E6'
                            : event.severity === 'WARN'
                              ? '#FBF0D9'
                              : '#F1EFEA',
                      padding: '2px 6px',
                      borderRadius: 5,
                    }}
                  >
                    {event.chip}
                  </span>
                )}
              </div>
              <span style={{ fontSize: 13.5, color: '#1A1A17', lineHeight: 1.45 }}>{event.text}</span>
            </div>
            <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: '#A8A299', flex: '0 0 auto', paddingTop: 2 }}>{clockTime(event.at)}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
