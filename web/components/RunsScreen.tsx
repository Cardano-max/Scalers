'use client';

import { useEffect, useMemo, useState } from 'react';
import { useData } from '@/lib/data/DataProvider';
import { useAsync } from '@/lib/useAsync';
import { useConsole } from '@/state/console-store';
import { Dot } from './icons';
import { Chip, clockTime } from './console-bits';
import { WORKER_COLOR } from '@/lib/tokens';
import type { Run, CampaignSpec } from '@/lib/data/models';
import { SpanTree } from './trace/SpanTree';
import { SpecMarkdown } from './SpecMarkdown';
import { resolveSelectedId, isDeepLinkHit } from '@/lib/trace-select';
import { useTraceArrival } from '@/lib/useTraceArrival';
import { LineageChips } from './trace/LineageChips';

export function RunsScreen() {
  const { adapter, tenantId } = useData();
  const console = useConsole();
  const runs = useAsync<Run[]>(() => adapter.getRuns(tenantId), [tenantId]);

  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [openEventIndex, setOpenEventIndex] = useState<number | null>(null);
  const { highlightId, trigger: triggerArrival, scrollRef } = useTraceArrival();

  // Per-campaign spec doc — lazy-loaded for the selected run on demand.
  const [specOpen, setSpecOpen] = useState(false);
  const [specLoading, setSpecLoading] = useState(false);
  const [specError, setSpecError] = useState<string | null>(null);
  const [spec, setSpec] = useState<CampaignSpec | null>(null);

  // Reset the spec panel whenever the selected run changes.
  useEffect(() => {
    setSpecOpen(false);
    setSpec(null);
    setSpecError(null);
    setSpecLoading(false);
  }, [selectedRunId]);

  const loadSpec = (runId: string) => {
    if (specOpen) {
      setSpecOpen(false);
      return;
    }
    setSpecOpen(true);
    if (spec || specLoading) return;
    setSpecLoading(true);
    setSpecError(null);
    adapter
      .getCampaignSpec(runId)
      .then((s) => setSpec(s))
      .catch((e) => setSpecError(e instanceof Error ? e.message : String(e)))
      .finally(() => setSpecLoading(false));
  };

  const items = useMemo(() => runs.data ?? [], [runs.data]);

  // SINGLE selection resolver (same fix as ActivityScreen): a deep-link run id
  // (contextId) from the live feed "Open run →" / a feed pill / an Overview
  // RunRow ALWAYS wins over the items[0] default. The old two-effect form raced
  // on the data-load commit and the default clobbered the target → every "Open
  // run" landed on the first run. On a real hit we open that run cleanly and
  // fire the scroll+pulse arrival highlight.
  useEffect(() => {
    const target = console.contextId;
    const hit = isDeepLinkHit(items, target);
    const next = resolveSelectedId(items, target, selectedRunId);
    if (next !== selectedRunId) {
      setSelectedRunId(next);
      setOpenEventIndex(null);
    }
    if (hit && target) {
      triggerArrival(target);
      console.setContext(null);
    }
  }, [items, selectedRunId, console, triggerArrival]);

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
                ref={r.id === highlightId ? scrollRef : undefined}
                className={r.id === highlightId ? 'trace-arrive' : undefined}
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
          <div style={{ fontSize: 12, color: '#8C877D', marginBottom: 10 }}>
            {selected.trigger} · {clockTime(selected.startedAt)}
          </div>
          {/* Lineage chips — campaign + run + run-level trace, deep-linkable. */}
          <div style={{ marginBottom: 16 }}>
            <LineageChips
              lineage={{
                campaignId: selected.campaignId,
                runId: selected.id,
                createdAt: selected.startedAt,
                channel: selected.channels[0] ?? null,
                traceUrl: selected.traceUrl,
              }}
            />
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

          {/* Spec doc + Langfuse trace link */}
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 18 }}>
            <button
              type="button"
              onClick={() => loadSpec(selected.id)}
              style={{
                fontSize: 12.5,
                fontWeight: 600,
                color: specOpen ? '#fff' : '#0B6F68',
                background: specOpen ? '#0B6F68' : '#fff',
                border: '1px solid #0B6F68',
                padding: '8px 13px',
                borderRadius: 8,
                cursor: 'pointer',
              }}
            >
              {specOpen ? 'Hide spec ▲' : 'View spec ▾'}
            </button>
            {selected.traceUrl ? (
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
            ) : null}
          </div>

          {/* Spec doc panel — real assembled markdown, or honest-null/empty. */}
          {specOpen && (
            <div
              style={{
                marginBottom: 18,
                border: '1px solid var(--hairline, #E0DCD3)',
                borderRadius: 10,
                background: '#FBFAF7',
                padding: '14px 15px',
                maxHeight: 360,
                overflowY: 'auto',
              }}
            >
              {specLoading ? (
                <div style={{ fontSize: 12.5, color: '#8C877D' }}>Assembling spec…</div>
              ) : specError ? (
                <div style={{ fontSize: 12.5, color: '#B42318' }}>
                  Could not load the spec: {specError}
                </div>
              ) : spec && spec.markdown ? (
                <SpecMarkdown markdown={spec.markdown} />
              ) : (
                <div style={{ fontSize: 12.5, color: '#8C877D', lineHeight: 1.5 }}>
                  No spec doc has been assembled for this run yet — it had no
                  persisted plan/agent traces to build one from. Nothing was
                  fabricated to fill this in.
                </div>
              )}
            </div>
          )}

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
                  {openEventIndex === idx && (
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
                      {event.spans && event.spans.length > 0 ? (
                        <SpanTree spans={event.spans} dense />
                      ) : (
                        <p
                          style={{
                            fontFamily: "'IBM Plex Mono', monospace",
                            fontSize: 11,
                            color: '#8C877D',
                            lineHeight: 1.5,
                            margin: 0,
                          }}
                        >
                          No sub-step trace was captured for this step — only the step label, worker and duration above are recorded.
                          {event.actionId && ' Open the action for its full reasoning trace.'}
                        </p>
                      )}

                      {/* Nav buttons */}
                      <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginTop: 2 }}>
                        <button
                          type="button"
                          disabled={!event.actionId}
                          onClick={() => {
                            if (event.actionId) {
                              console.navigate('step_detail', event.actionId);
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
                          Open reasoning →
                        </button>
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
