'use client';

/**
 * NotCapturedBadge — renders a small grey "not captured yet" pill for fields
 * that have no real data source (model, tokens, latency "—"; tool/MCP calls;
 * RAG/KB chunks; etc.).
 *
 * HONESTY RULE (spec §5): these fields MUST be badged, never printed as data.
 * The tooltip cites the backend line so engineers can trace the origin.
 *
 * Props:
 *   label — short human name shown inside the pill, e.g. "model", "tokens"
 */

interface NotCapturedBadgeProps {
  /** Short label displayed in the pill (e.g. "model", "tokens", "latency"). */
  label: string;
}

export function NotCapturedBadge({ label }: NotCapturedBadgeProps) {
  return (
    <span
      title={`${label}: not captured yet — repo.py:486-487 hardcodes "—"; no real source exists`}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 3,
        fontSize: 10,
        fontWeight: 500,
        color: '#8C877D',
        background: '#F1EFEA',
        border: '1px solid #E0DDD6',
        borderRadius: 4,
        padding: '1px 6px',
        fontFamily: "'IBM Plex Mono', monospace",
        cursor: 'default',
        whiteSpace: 'nowrap',
      }}
    >
      {label}
      <span style={{ opacity: 0.6 }}>· not captured yet</span>
    </span>
  );
}
