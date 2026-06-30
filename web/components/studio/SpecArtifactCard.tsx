'use client';

/**
 * SpecArtifactCard — the "Open Campaign Spec" artifact that materializes when the
 * jury completes. The campaign spec is REAL: campaignSpec(runId) → assembled
 * markdown (Goal / Audience / Channels / Success / Team-per-role / Schedule /
 * Step-log) rendered in a focused ~68ch reading column. The footer is an HONEST
 * lifecycle ribbon — Spec written → Drafts HELD (amber) → Review Queue → approve →
 * Sent — and nothing auto-advances. The Review-Queue approve mutation is the ONLY
 * send path. Langfuse chip is gated on a real traceUrl.
 */
import { useState } from 'react';
import { useData } from '@/lib/data/DataProvider';
import { SpecMarkdown } from '../SpecMarkdown';
import type { CampaignSpec } from '@/lib/data/models';

const TEAL = '#0F8A82';

export function SpecArtifactCard({
  runId,
  nPending,
  traceUrl,
  onOpenReview,
}: {
  runId: string;
  nPending: number | null;
  traceUrl?: string | null;
  onOpenReview?: () => void;
}) {
  const { adapter } = useData();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [spec, setSpec] = useState<CampaignSpec | null>(null);
  const [loaded, setLoaded] = useState(false);

  const toggle = () => {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (loaded || loading) return;
    setLoading(true);
    setError(null);
    adapter
      .getCampaignSpec(runId)
      .then((s) => setSpec(s))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => {
        setLoading(false);
        setLoaded(true);
      });
  };

  return (
    <section
      aria-label="Campaign spec artifact"
      className="spring-in"
      style={{
        background: '#fff',
        border: '1px solid var(--hairline-strong)',
        borderRadius: 'var(--radius-card)',
        overflow: 'hidden',
        boxShadow: 'var(--shadow-selected)',
      }}
    >
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: '14px 16px',
          borderBottom: open ? '1px solid var(--hairline)' : 'none',
        }}
      >
        <span
          aria-hidden
          style={{
            width: 34,
            height: 34,
            borderRadius: 9,
            background: 'var(--success-bg)',
            color: 'var(--success-text)',
            display: 'grid',
            placeItems: 'center',
            flex: '0 0 auto',
          }}
        >
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <path d="M14 2v6h6M9 13h6M9 17h6" />
          </svg>
        </span>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 590, color: 'var(--ink)' }}>Campaign spec written</div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            Assembled from the real run · <span style={{ fontFamily: 'var(--font-mono)' }}>{runId}</span>
          </div>
        </div>
        <button
          type="button"
          onClick={toggle}
          style={{
            fontSize: 12.5,
            fontWeight: 590,
            color: '#fff',
            background: TEAL,
            border: 'none',
            padding: '8px 14px',
            borderRadius: 'var(--radius-button)',
            cursor: 'pointer',
          }}
        >
          {open ? 'Close spec' : 'Open Campaign Spec'}
        </button>
      </header>

      {open && (
        <div style={{ padding: '18px 16px', background: 'var(--canvas)' }}>
          {/* Focused reading column ~68ch. */}
          <div
            style={{
              maxWidth: '68ch',
              margin: '0 auto',
              background: '#fff',
              border: '1px solid var(--hairline)',
              borderRadius: 10,
              padding: '20px 24px',
              minHeight: 80,
            }}
          >
            {loading ? (
              <div style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>Assembling spec…</div>
            ) : error ? (
              <div style={{ fontSize: 12.5, color: 'var(--danger-text)' }}>Could not load the spec: {error}</div>
            ) : spec && spec.markdown ? (
              <SpecMarkdown markdown={spec.markdown} />
            ) : (
              <div style={{ fontSize: 12.5, color: 'var(--text-muted)', lineHeight: 1.5 }}>
                Spec not written yet — this run has no persisted plan/agent traces to assemble one from. Nothing was fabricated to fill this in.
              </div>
            )}
          </div>

          {/* Honest lifecycle ribbon. */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              flexWrap: 'wrap',
              justifyContent: 'center',
              marginTop: 18,
            }}
          >
            <RibbonStep label="Spec written" tone="done" />
            <RibbonArrow />
            <RibbonStep
              label={nPending != null ? `${nPending} draft${nPending === 1 ? '' : 's'} HELD` : 'Drafts HELD'}
              tone="amber"
            />
            <RibbonArrow />
            <RibbonStep
              label="Review queue"
              tone="link"
              onClick={onOpenReview}
            />
            <RibbonArrow />
            <RibbonStep label="Operator approves" tone="muted" />
            <RibbonArrow />
            <RibbonStep label="Sent" tone="muted" />
          </div>
          <p style={{ textAlign: 'center', fontSize: 11.5, color: 'var(--text-faint)', marginTop: 10, marginBottom: 0 }}>
            Nothing auto-advances. Approving in the Review queue is the only send path.
          </p>

          {traceUrl ? (
            <div style={{ textAlign: 'center', marginTop: 12 }}>
              <a
                href={traceUrl}
                target="_blank"
                rel="noreferrer noopener"
                style={{
                  display: 'inline-block',
                  fontSize: 12,
                  fontWeight: 560,
                  color: 'var(--teal-dark)',
                  textDecoration: 'none',
                  border: '1px solid var(--hairline-strong)',
                  borderRadius: 'var(--radius-button)',
                  padding: '6px 12px',
                }}
              >
                View Langfuse trace ↗
              </a>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

function RibbonStep({
  label,
  tone,
  onClick,
}: {
  label: string;
  tone: 'done' | 'amber' | 'link' | 'muted';
  onClick?: () => void;
}) {
  const style: React.CSSProperties = {
    fontSize: 11.5,
    fontWeight: 590,
    padding: '4px 10px',
    borderRadius: 'var(--radius-pill)',
    border: '1px solid var(--hairline)',
    fontVariantNumeric: 'tabular-nums',
    background: 'var(--surface-alt)',
    color: 'var(--text-muted)',
    whiteSpace: 'nowrap',
  };
  if (tone === 'done') {
    style.background = 'var(--success-bg)';
    style.color = 'var(--success-text)';
    style.borderColor = '#C7E8D4';
  } else if (tone === 'amber') {
    style.background = 'var(--amber-bg)';
    style.color = 'var(--amber-text)';
    style.borderColor = 'var(--amber-border)';
  } else if (tone === 'link') {
    style.color = 'var(--teal-dark)';
    style.borderColor = '#C9E5E1';
    style.cursor = onClick ? 'pointer' : 'default';
  }
  if (onClick && tone === 'link') {
    return (
      <button type="button" onClick={onClick} style={{ ...style, font: 'inherit' }}>
        {label}
      </button>
    );
  }
  return <span style={style}>{label}</span>;
}

function RibbonArrow() {
  return <span aria-hidden style={{ color: 'var(--text-faint)', fontSize: 12 }}>→</span>;
}
