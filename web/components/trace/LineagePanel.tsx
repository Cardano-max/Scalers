'use client';

/**
 * Draft lineage panel (ju1.5, operator order #10) — WHERE this draft came from:
 * source CSV file, the customer (name / email / phone), the artist + studio, the
 * campaign example(s) referenced, the offer, the CTA, and the channel. Bound to
 * GET /studio/action/{id}/lineage through the adapter.
 *
 * Honesty contract: a field the engine could not ground renders an explicit
 * "missing" — never a blank and never a fabricated value. `examples` is empty
 * until ju1.4 wires per-draft example provenance, and the panel SAYS so; example
 * entries are clickable and deep-link to the Campaign memory screen.
 */
import { useData } from '@/lib/data/DataProvider';
import { useConsoleOptional } from '@/state/console-store';
import { useAsync } from '@/lib/useAsync';
import { Chip } from '../console-bits';
import type { ActionLineage } from '@/lib/data/models';

function Missing({ why }: { why?: string }) {
  return (
    <span style={{ fontSize: 12, color: 'var(--text-muted)', fontStyle: 'italic' }}>
      missing{why ? ` — ${why}` : ''}
    </span>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'baseline' }}>
      <span
        className="label"
        style={{ width: 130, flexShrink: 0, fontSize: 10.5, color: 'var(--text-muted)' }}
      >
        {label}
      </span>
      <span style={{ fontSize: 12.5, overflowWrap: 'anywhere' }}>{children}</span>
    </div>
  );
}

export function LineagePanel({ actionId }: { actionId: string }) {
  const { adapter } = useData();
  const consoleCtx = useConsoleOptional();
  const { data, loading } = useAsync<ActionLineage | null>(
    // Tolerate partial fake adapters (older tests): no method -> honest
    // "no lineage recorded", identical to a transport failure.
    () =>
      typeof adapter.getActionLineage === 'function'
        ? adapter.getActionLineage(actionId)
        : Promise.resolve(null),
    [adapter, actionId],
  );

  if (loading) return null; // render only once resolved (no flash), like evidence
  const lin = data ?? null;

  return (
    <section
      aria-label="Draft lineage"
      style={{
        border: '1px solid var(--hairline)',
        borderRadius: 'var(--radius-card)',
        background: 'var(--surface)',
        padding: '12px 14px',
        display: 'grid',
        gap: 7,
      }}
    >
      <div style={{ fontSize: 12.5, fontWeight: 700 }}>Lineage — where this draft came from</div>
      {lin === null ? (
        <Missing why="no lineage recorded for this draft" />
      ) : (
        <>
          <Row label="Source file">
            {lin.sourceFile ?? <Missing why="not recorded on the customer row" />}
          </Row>
          <Row label="Customer">
            {lin.customer.name || lin.customer.email || lin.customer.phone ? (
              <>
                {lin.customer.name ?? <Missing />}
                {lin.customer.email ? (
                  <span className="mono" style={{ fontSize: 11.5, marginLeft: 8 }}>
                    {lin.customer.email}
                  </span>
                ) : null}
                {lin.customer.phone ? (
                  <span className="mono" style={{ fontSize: 11.5, marginLeft: 8 }}>
                    {lin.customer.phone}
                  </span>
                ) : null}
              </>
            ) : (
              <Missing why="no grounded customer identity" />
            )}
          </Row>
          <Row label="Artist">{lin.artist ?? <Missing why="customer has no artist on file" />}</Row>
          <Row label="Studio">{lin.studio ?? <Missing why="no studio mapping for this lead" />}</Row>
          <Row label="Campaign example">
            {lin.examples.length > 0 ? (
              lin.examples.map((ex) => (
                <button
                  key={ex.id}
                  type="button"
                  onClick={() => consoleCtx?.navigate('memory', ex.id)}
                  style={{
                    font: 'inherit', fontSize: 12.5, color: 'var(--accent-dark, var(--accent))',
                    background: 'none', border: 'none', padding: 0, cursor: 'pointer',
                    textDecoration: 'underline', marginRight: 10,
                  }}
                >
                  {ex.campaign_name}
                </button>
              ))
            ) : (
              <Missing why="per-draft example provenance lands with ju1.4" />
            )}
          </Row>
          <Row label="Offer">{lin.offer ?? <Missing why="no substantiated offer used" />}</Row>
          <Row label="CTA">{lin.cta ?? <Missing />}</Row>
          <Row label="Channel">
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
              {lin.channel ?? <Missing />}
              {String(lin.channel).toLowerCase() === 'sms' ? (
                <Chip tone="neutral">no SMS send path yet</Chip>
              ) : null}
            </span>
          </Row>
          {lin.limitedPersonalization ? (
            <div style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
              Limited personalization{lin.personalizationNote ? `: ${lin.personalizationNote}` : ''}
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
