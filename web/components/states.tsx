/**
 * Loading / empty / error primitives. A kkg.4 edge-case requirement: never show
 * a blank screen. Every binding renders a Skeleton while loading, an EmptyState
 * when the engine has no data yet, and an ErrorState (with retry) when a query
 * or the stream fails.
 */
'use client';

import type { ReactNode } from 'react';

export function Skeleton({ rows = 3, label = 'Loading…' }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-busy="true" aria-label={label} style={{ padding: 'var(--pad-card)' }}>
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          style={{
            height: 14,
            margin: '10px 0',
            borderRadius: 6,
            background: 'var(--hairline-light)',
            width: `${90 - i * 12}%`,
          }}
        />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  hint,
}: {
  title: string;
  hint?: string;
}) {
  return (
    <div
      style={{
        padding: 'var(--pad-section)',
        textAlign: 'center',
        color: 'var(--text-muted)',
      }}
    >
      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-secondary)' }}>{title}</div>
      {hint ? <div style={{ marginTop: 6, fontSize: 12 }}>{hint}</div> : null}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
}: {
  error: Error;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      style={{
        padding: 'var(--pad-card)',
        border: '1px solid var(--amber-border)',
        background: 'var(--danger-bg)',
        color: 'var(--danger-text)',
        borderRadius: 'var(--radius-card)',
        margin: 'var(--pad-card)',
      }}
    >
      <div style={{ fontWeight: 600 }}>Couldn’t load this view</div>
      <div className="mono" style={{ fontSize: 12, marginTop: 6, opacity: 0.85 }}>
        {error.message}
      </div>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          style={{
            marginTop: 10,
            border: '1px solid var(--danger-text)',
            background: 'transparent',
            color: 'var(--danger-text)',
            borderRadius: 'var(--radius-button)',
            padding: '6px 12px',
            cursor: 'pointer',
          }}
        >
          Retry
        </button>
      ) : null}
    </div>
  );
}

/**
 * Wrap a binding: shows Skeleton/Error/Empty/loaded for an async value, so a
 * screen never has to hand-roll the four branches.
 */
export function AsyncBoundary<T>({
  loading,
  error,
  data,
  empty,
  onRetry,
  emptyTitle = 'Nothing here yet',
  emptyHint,
  children,
}: {
  loading: boolean;
  error: Error | undefined;
  data: T | undefined;
  empty: boolean;
  onRetry?: () => void;
  emptyTitle?: string;
  emptyHint?: string;
  children: (data: T) => ReactNode;
}) {
  if (loading && data === undefined) return <Skeleton />;
  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  if (data === undefined) return <Skeleton />;
  if (empty) return <EmptyState title={emptyTitle} hint={emptyHint} />;
  return <>{children(data)}</>;
}
