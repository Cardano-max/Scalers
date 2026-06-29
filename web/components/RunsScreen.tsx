'use client';

import { useEffect, useMemo, useState } from 'react';
import { useData } from '@/lib/data/DataProvider';
import { useAsync } from '@/lib/useAsync';
import { useConsole } from '@/state/console-store';
import { Dot } from './icons';
import { Chip, clockTime } from './console-bits';
import { WORKER_COLOR } from '@/lib/tokens';
import type { Run, Span } from '@/lib/data/models';

const SPAN_KIND_STYLE: Record<Span['kind'], { color: string; bg: string }> = {
  tool: { color: '#0B6F68', bg: '#E1F1EF' },
  llm: { color: '#0B6F68', bg: '#E1F1EF' },
  jury: { color: '#9A6B00', bg: '#FBF0D9' },
  gate: { color: '#157F4B', bg: '#E6F4EC' },
  decision: { color: '#157F4B', bg: '#E6F4EC' },
};

export function RunsScreen() {
  const { adapter, tenantId } = useData();
  const console = useConsole();
  const runs = useAsync<Run[]>(() => adapter.getRuns(tenantId), [tenantId]);

  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [openEventIndex, setOpenEventIndex] = useState<number | null>(null);

  const items = useMemo(() => runs.data ?? [], [runs.data]);

  useEffect(() => {
    if (items.length > 0 && !selectedRunId) {
      setSelectedRunId(items[0].id);
    }
  }, [items, selectedRunId]);

  const selected = items.find((r) => r.id === selectedRunId) ?? null;

  return (
    <div style={{ display: 'flex', height: '100%', minHeight: 0 }}>
      {/* LIST */}
      <div
        style={{
          flex: 1,
          minWidth: 0,
          overflowY: 'auto',
          padding: '24px 22px 44px',
          background: 'var(--surface)',
        }}
      >
        <div style={{ padding: '0 4px 13px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: 10, fontFamily: "'IBM Plex Mono', monospace", color: '#A8A299', letterSpacing: '0.7px' }}>WORKFLOW RUNS</span>
          <span style={{ fontSize: 11, fontFamily: "'IBM Plex Mono', monospace", color: '#A8A299' }}>durable · checkpointed · exactly-once</span>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {items.map((r) => {
            const isSelected = r.id === selectedRunId;
            const statusColor =
              r.status === 'SUCCESS' ? '#157F4B' : r.status === 'FAILED' ? '#B42318' : '#0B6F68';
            return (
              <button
                key={r.id}
                type="button"
                onClick={() => {
                  setSelectedRunId(r.id);
                  setOpenEventIndex(null);
                }}
                style={{
                  display: 'flex',
                  gap: 13,
                  alignItems: 'flex-start',
                  padding: '13px 12px',
                  borderRadius: 9,
                  border: isSelected ? '1px solid var(--accent)' : 'none',
                  background: isSelected ? 'var(--nav-active-bg)' : 'transparent',
                  cursor: 'pointer',
                  textAlign: 'left',
                  minWidth: 0,
                  transition: 'background 0.15s',
                }}
                style-hover={{ background: 'var(--surface-hover, #F7F6F1)' }}
              >
                <Dot color={statusColor} />
                <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 9, minWidth: 0 }}>
                    <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 12.5, fontWeight: 600, color: '#0B6F68', flex: '0 0 auto' }}>{r.id}</span>
                    <span style={{ fontSize: 13.5, fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.type}</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap', fontSize: 11.5, color: '#8C877D' }}>
                    <span style={{ fontFamily: "'IBM Plex Mono', monospace" }}>{r.trigger}</span>
                    <span style={{ color: '#D8D3C9' }}>·</span>
                    <span>{clockTime(r.startedAt)}</span>
                    <span style={{ color: '#D8D3C9' }}>·</span>
                    <span>{r.duration || 'pending'}</span>
                  </div>
                </div>
                <Chip tone={r.status === 'SUCCESS' ? 'success' : r.status === 'FAILED' ? 'danger' : 'neutral'}>{r.status}</Chip>
              </button>
            );
          })}
        </div>
      </div>

      {/* DETAIL DRAWER */}
      {selected && (
        <aside
          style={{
            width: 412,
            flex: '0 0 412px',
            borderLeft: '1px solid var(--hairline)',
            background: '#fff',
            overflowY: 'auto',
            height: '100%',
            padding: '24px 22px 40px',
          }}
        >
          {/* Header */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 5 }}>
            <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 14, fontWeight: 600, color: '#0B6F68' }}>{selected.id}</span>
            <span style={{ flex: 1 }} />
            <Chip tone={selected.status === 'SUCCESS' ? 'success' : selected.status === 'FAILED' ? 'danger' : 'neutral'}>{selected.status}</Chip>
          </div>
          <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 3 }}>{selected.type}</div>
          <div style={{ fontSize: 12, color: '#8C877D', marginBottom: 18 }}>
            {selected.trigger} · {clockTime(selected.startedAt)}
          </div>

          {/* Meta grid */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '13px 12px', marginBottom: 20 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              <span style={{ fontSize: 10, fontFamily: "'IBM Plex Mono', monospace", color: '#A8A299', letterSpacing: '0.5px' }}>TENANT</span>
              <span style={{ fontSize: 13, fontWeight: 500 }}>{selected.tenantId === 'ladies8391' ? 'Ladies First' : selected.tenantId}</span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              <span style={{ fontSize: 10, fontFamily: "'IBM Plex Mono', monospace", color: '#A8A299', letterSpacing: '0.5px' }}>CHANNELS</span>
              <span style={{ fontSize: 13, fontWeight: 500 }}>{selected.channels.join(' · ')}</span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              <span style={{ fontSize: 10, fontFamily: "'IBM Plex Mono', monospace", color: '#A8A299', letterSpacing: '0.5px' }}>AUTONOMY</span>
              <span style={{ fontSize: 13, fontWeight: 500 }}>{selected.autoCount} auto · {selected.reviewCount} review</span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              <span style={{ fontSize: 10, fontFamily: "'IBM Plex Mono', monospace", color: '#A8A299', letterSpacing: '0.5px' }}>DURATION</span>
              <span style={{ fontSize: 13, fontWeight: 500 }}>{selected.duration || 'running'}</span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              <span style={{ fontSize: 10, fontFamily: "'IBM Plex Mono', monospace", color: '#A8A299', letterSpacing: '0.5px' }}>RETRIES</span>
              <span style={{ fontSize: 13, fontWeight: 500 }}>{selected.retries}</span>
            </div>
          </div>

          {/* Langfuse trace link */}
          {selected.traceUrl ? (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 18 }}>
              <button
                type="button"
                onClick={() => {
                  if (selected.traceUrl) {
                    window.open(selected.traceUrl, '_blank');
                  }
                }}
                style={{
                  fontSize: 12.5,
                  fontWeight: 500,
                  color: '#0B6F68',
                  background: '#fff',
                  border: '1px solid #D8D3C9',
                  padding: '8px 13px',
                  borderRadius: 8,
                  cursor: 'pointer',
                  textDecoration: 'none',
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.background = '#F1EFEA';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.background = '#fff';
                }}
              >
                View Langfuse trace ↗
              </button>
            </div>
          ) : null}

          {/* Run History */}
          <div style={{ fontSize: 10, fontFamily: "'IBM Plex Mono', monospace", color: '#A8A299', letterSpacing: '0.7px', marginBottom: 11 }}>RUN HISTORY · tap a step for its trace</div>

          <div style={{ display: 'flex', flexDirection: 'column' }}>
            {(selected.events || []).map((event, idx) => (
              <div key={idx} style={{ display: 'flex', gap: 11, alignItems: 'flex-start' }}>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flex: '0 0 auto', alignSelf: 'stretch' }}>
                  <Dot color={WORKER_COLOR[event.worker] || '#8C877D'} />
                  {idx < (selected.events?.length || 0) - 1 && (
                    <div
                      style={{
                        flex: 1,
                        width: 1,
                        background: '#D8D3C9',
                        margin: '6px 0',
                      }}
                    />
                  )}
                </div>
                <div style={{ flex: 1, minWidth: 0, paddingBottom: 13 }}>
                  <button
                    type="button"
                    onClick={() => setOpenEventIndex(openEventIndex === idx ? null : idx)}
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: 8,
                      width: '100%',
                      textAlign: 'left',
                      background: 'none',
                      padding: 0,
                      cursor: 'pointer',
                      border: 'none',
                      color: 'inherit',
                    }}
                    onMouseEnter={(e) => {
                      (e.currentTarget as HTMLElement).style.opacity = '0.65';
                    }}
                    onMouseLeave={(e) => {
                      (e.currentTarget as HTMLElement).style.opacity = '1';
                    }}
                  >
                    <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 3 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 11, fontWeight: 600, color: WORKER_COLOR[event.worker] || '#8C877D', textTransform: 'uppercase' }}>{event.worker}</span>
                        {event.severity && event.severity !== 'INFO' && (
                          <span
                            style={{
                              fontSize: 10,
                              fontWeight: 600,
                              color: event.severity === 'SUCCESS' ? '#157F4B' : event.severity === 'WARN' ? '#9A6B00' : '#B42318',
                              background:
                                event.severity === 'SUCCESS'
                                  ? '#E6F4EC'
                                  : event.severity === 'WARN'
                                    ? '#FBF0D9'
                                    : '#FBE9E6',
                              padding: '2px 6px',
                              borderRadius: 5,
                            }}
                          >
                            {event.severity}
                          </span>
                        )}
                        <span style={{ flex: 1 }} />
                        {event.ms && <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10, color: '#BDB8AD', flex: '0 0 auto' }}>{event.ms}</span>}
                      </div>
                      <span style={{ fontSize: 13, color: '#1A1A17', lineHeight: 1.4 }}>{event.text}</span>
                    </div>
                    <span style={{ fontSize: 11, color: '#A8A299', flex: '0 0 auto', marginTop: 2 }}>{openEventIndex === idx ? '▼' : '▶'}</span>
                  </button>

                  {/* Expanded span tree */}
                  {openEventIndex === idx && event.spans && event.spans.length > 0 && (
                    <div
                      style={{
                        marginTop: 11,
                        borderLeft: '2px solid #DCEDEA',
                        paddingLeft: 12,
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 12,
                      }}
                    >
                      {event.spans.map((span, sidx) => (
                        <div key={sidx} style={{ display: 'flex', gap: 9, alignItems: 'flex-start' }}>
                          <span
                            style={{
                              width: 6,
                              height: 6,
                              borderRadius: '50%',
                              background: SPAN_KIND_STYLE[span.kind]?.color || '#8C877D',
                              flex: '0 0 auto',
                              marginTop: 5,
                            }}
                          />
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 3 }}>
                              <span
                                style={{
                                  fontSize: 9,
                                  fontWeight: 600,
                                  color: SPAN_KIND_STYLE[span.kind]?.color || '#8C877D',
                                  background: SPAN_KIND_STYLE[span.kind]?.bg || '#F1EFEA',
                                  padding: '2px 6px',
                                  borderRadius: 5,
                                  textTransform: 'uppercase',
                                }}
                              >
                                {span.kind}
                              </span>
                              <span style={{ fontSize: 12, fontWeight: 600, color: '#1A2E2B' }}>{span.title}</span>
                              <span style={{ flex: 1 }} />
                              {span.ms && <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 9.5, color: '#9BBFBB', flex: '0 0 auto' }}>{span.ms}ms</span>}
                            </div>
                            <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: '#5C7A76', lineHeight: 1.5 }}>{span.detail}</div>
                          </div>
                        </div>
                      ))}

                      {/* Nav buttons */}
                      <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginTop: 2 }}>
                        <button
                          type="button"
                          disabled={!event.actionId}
                          onClick={() => {
                            if (event.actionId) {
                              console.navigate('activity', event.actionId);
                            }
                          }}
                          style={{
                            fontSize: 11.5,
                            fontWeight: 600,
                            color: event.actionId ? '#0B6F68' : '#BDB8AD',
                            background: '#fff',
                            border: `1px solid ${event.actionId ? '#C9E5E1' : '#E0DCD3'}`,
                            padding: '6px 10px',
                            borderRadius: 8,
                            cursor: event.actionId ? 'pointer' : 'not-allowed',
                            opacity: event.actionId ? 1 : 0.5,
                          }}
                          onMouseEnter={(e) => {
                            if (event.actionId) {
                              (e.currentTarget as HTMLElement).style.background = '#F1EFEA';
                            }
                          }}
                          onMouseLeave={(e) => {
                            (e.currentTarget as HTMLElement).style.background = '#fff';
                          }}
                        >
                          Open in Activity →
                        </button>
                        <button
                          type="button"
                          disabled={!selected}
                          onClick={() => {
                            if (selected) {
                              console.navigate('feed', selected.id);
                            }
                          }}
                          style={{
                            fontSize: 11.5,
                            fontWeight: 500,
                            color: selected ? '#46423B' : '#BDB8AD',
                            background: '#fff',
                            border: `1px solid ${selected ? '#E0DCD3' : '#E0DCD3'}`,
                            padding: '6px 10px',
                            borderRadius: 8,
                            cursor: selected ? 'pointer' : 'not-allowed',
                            opacity: selected ? 1 : 0.5,
                          }}
                          onMouseEnter={(e) => {
                            if (selected) {
                              (e.currentTarget as HTMLElement).style.background = '#F1EFEA';
                            }
                          }}
                          onMouseLeave={(e) => {
                            (e.currentTarget as HTMLElement).style.background = '#fff';
                          }}
                        >
                          Show in live feed →
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>

          {/* Note */}
          {selected.note && (
            <div
              style={{
                marginTop: 6,
                background: '#FBF0D9',
                border: '1px solid #EAD6A8',
                borderRadius: 10,
                padding: '12px 13px',
                fontSize: 12.5,
                color: '#7A5A12',
                lineHeight: 1.5,
              }}
            >
              {selected.note}
            </div>
          )}
        </aside>
      )}
    </div>
  );
}
