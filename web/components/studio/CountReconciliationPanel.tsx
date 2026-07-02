'use client';

/**
 * CountReconciliationPanel (CustomerAcq-sgr) — the draft-count reconciliation for a run:
 * requested / created / in-review-queue / skipped / failed, with the EXACT per-row
 * reasons. It renders campaign_state.reconciliation VERBATIM (DB-derived, credit-
 * independent), so the number the operator sees here always equals the number the
 * review queue holds and the voice supervisor reports — never "2 when the UI shows 10".
 *
 * Honest by construction: it shows `reconciled` only when every requested row is
 * accounted for (created + skipped + failed ≥ expected); an undercount is surfaced,
 * not hidden.
 */
import type { CSSProperties } from 'react';
import type { Reconciliation, ReconcileRow } from '@/lib/studio/run-trace';

const TILE: CSSProperties = {
  flex: '1 1 0',
  minWidth: 74,
  padding: '8px 10px',
  borderRadius: 8,
  border: '1px solid var(--hairline)',
  background: 'var(--surface)',
  textAlign: 'center',
};

function Tile({ label, value, tone }: { label: string; value: number; tone?: 'amber' | 'danger' | 'teal' }) {
  const color =
    tone === 'amber' ? 'var(--amber-text)' : tone === 'danger' ? 'var(--danger-text)' : tone === 'teal' ? 'var(--auto-chip-text)' : 'var(--ink)';
  return (
    <div style={TILE}>
      <div style={{ fontSize: 19, fontWeight: 600, color, lineHeight: 1.1 }}>{value}</div>
      <div className="mono" style={{ fontSize: 9.5, textTransform: 'uppercase', letterSpacing: 0.4, color: 'var(--text-muted)', marginTop: 3 }}>
        {label}
      </div>
    </div>
  );
}

function ReasonList({ title, rows, tone }: { title: string; rows: ReconcileRow[]; tone: 'amber' | 'danger' }) {
  if (rows.length === 0) return null;
  const color = tone === 'amber' ? 'var(--amber-text)' : 'var(--danger-text)';
  return (
    <div style={{ marginTop: 10 }}>
      <div className="mono" style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.4, color }}>
        {title} · {rows.length}
      </div>
      <ul style={{ listStyle: 'none', margin: '4px 0 0', padding: 0 }}>
        {rows.map((r, i) => (
          <li
            key={`${r.row ?? 'x'}-${i}`}
            style={{ fontSize: 12, color: 'var(--text-secondary)', padding: '2px 0', display: 'flex', gap: 6 }}
          >
            <span className="mono" style={{ color: 'var(--text-muted)', flex: '0 0 auto' }}>
              {r.row != null ? `row ${r.row}` : r.lead ? String(r.lead) : '—'}
            </span>
            <span>{r.reason}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function CountReconciliationPanel({ reconciliation }: { reconciliation: Reconciliation | null | undefined }) {
  if (!reconciliation) return null;
  const r = reconciliation;
  const unaccounted = Math.max(0, r.expected - r.accounted);
  return (
    <section
      aria-label="Draft count reconciliation"
      style={{
        border: '1px solid var(--hairline)',
        borderRadius: 'var(--radius-card, 12px)',
        background: 'var(--surface-alt)',
        padding: 14,
        boxShadow: 'var(--shadow-card)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <span className="mono" style={{ fontSize: 10.5, textTransform: 'uppercase', letterSpacing: 0.5, color: 'var(--text-muted)' }}>
          Draft count reconciliation
        </span>
        <span
          style={{
            marginLeft: 'auto',
            fontSize: 11,
            fontWeight: 600,
            padding: '2px 9px',
            borderRadius: 999,
            color: r.reconciled ? 'var(--success-text)' : 'var(--amber-text)',
            background: r.reconciled ? 'var(--success-bg)' : 'var(--amber-bg)',
            border: `1px solid ${r.reconciled ? 'var(--success-dot)' : 'var(--amber-border)'}`,
          }}
        >
          {r.reconciled ? '✓ Reconciled' : `${unaccounted} unaccounted`}
        </span>
      </div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Tile label="Requested" value={r.requested} />
        <Tile label="Created" value={r.created} tone="teal" />
        <Tile label="In queue" value={r.inQueue} />
        <Tile label="Skipped" value={r.skipped.length} tone={r.skipped.length ? 'amber' : undefined} />
        <Tile label="Failed" value={r.failed.length} tone={r.failed.length ? 'danger' : undefined} />
      </div>

      {/* The honest math: created + skipped + failed accounts for every requested row. */}
      <div style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 8 }}>
        {r.created} created + {r.skipped.length} skipped + {r.failed.length} failed = {r.accounted} of {r.expected} requested
        {r.reconciled ? ' — every row accounted for.' : ` — ${unaccounted} unexplained.`}
      </div>

      <ReasonList title="Skipped" rows={r.skipped} tone="amber" />
      <ReasonList title="Failed" rows={r.failed} tone="danger" />
    </section>
  );
}
