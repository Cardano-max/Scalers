'use client';

/**
 * ExecutionTraceCard — the teal "EXECUTION TRACE" card extracted verbatim from
 * ActivityScreen.tsx:312-386.
 *
 * HONESTY RULES (spec §5):
 *   - latency/model/tokens each route through <NotCapturedBadge> when the backend
 *     sends the literal placeholder "—" (repo.py:486-487 hardcodes these; no real
 *     data source exists for any of them).
 *   - When trace is null the metadata row (latency/model/tokens) is omitted
 *     entirely — never prints "latency —" or any derivative of a placeholder.
 *   - <SpanTree> maps spans exactly; never invents or filters rows.
 *
 * Props:
 *   trace — ExecutionTrace or null/undefined.  null = show header only, omit
 *            the metadata row (do NOT render "latency —").
 *   spans — Span[]; forwarded verbatim to <SpanTree>.
 */

import type { ExecutionTrace, Span } from '@/lib/data/models';
import { NotCapturedBadge } from './NotCapturedBadge';
import { SpanTree } from './SpanTree';

export interface ExecutionTraceCardProps {
  /** Execution trace object.  When null/undefined the metadata row is omitted. */
  trace: ExecutionTrace | null | undefined;
  /** Span list forwarded verbatim to <SpanTree>. */
  spans: Span[];
}

/**
 * Literal value the backend emits when a field has no real data source.
 * Detect this to suppress the value and render a badge instead.
 * Source: repo.py:486 (model), :487 (tokens), :188/:243 (latency).
 */
const PLACEHOLDER = '—';

/** Renders a single metadata field: real value as mono text, placeholder as badge. */
function MetaField({ label, value }: { label: string; value: string }) {
  if (value === PLACEHOLDER) {
    return <NotCapturedBadge label={label} />;
  }
  return (
    <div>
      {label} {value}
    </div>
  );
}

export function ExecutionTraceCard({ trace, spans }: ExecutionTraceCardProps) {
  return (
    <div
      style={{
        border: '1px solid #CDE7E4',
        borderRadius: 'var(--radius-card)',
        background: '#F4FAF9',
        padding: 'var(--pad-card)',
        display: 'grid',
        gap: 12,
      }}
    >
      {/* ── Header: "EXECUTION TRACE" label (left) + trace.id (right) ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span
          style={{
            fontSize: 10.5,
            fontFamily: "'IBM Plex Mono', monospace",
            color: '#0B6F68',
            letterSpacing: '0.7px',
            fontWeight: 600,
          }}
        >
          EXECUTION TRACE
        </span>
        <span style={{ flex: 1 }} />
        {trace != null && (
          <span
            style={{
              fontFamily: "'IBM Plex Mono', monospace",
              fontSize: 11,
              color: '#0B6F68',
            }}
          >
            {trace.id}
          </span>
        )}
      </div>

      {/*
       * ── Metadata row: latency / model / tokens ──
       * Omitted entirely when trace is null (spec §5 rule 2 + task T3).
       * Each field routes through <NotCapturedBadge> when the value equals "—".
       */}
      {trace != null && (
        <div
          style={{
            display: 'flex',
            gap: 16,
            flexWrap: 'wrap',
            alignItems: 'center',
            marginBottom: 12,
            paddingBottom: 12,
            borderBottom: '1px solid #DCEDEA',
            fontSize: 11,
            fontFamily: "'IBM Plex Mono', monospace",
            color: '#5C7A76',
          }}
        >
          <MetaField label="latency" value={trace.latency} />
          <MetaField label="model"   value={trace.model} />
          <MetaField label="tokens"  value={trace.tokens} />
        </div>
      )}

      {/*
       * ── Span tree ──
       * Returns null when spans is empty; caller owns the fallback text
       * (spec §1 "caller owns fallback").
       */}
      <SpanTree spans={spans} />
    </div>
  );
}
