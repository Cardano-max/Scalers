'use client';

/**
 * Campaign Memory (ju1.5) — the tenant's REAL past-campaign example library
 * (ju1.2): per-campaign metrics, the actual message copy, and the source
 * screenshot each example was transcribed from (streamed from the LOCAL
 * client-data dir — nothing is uploaded anywhere). Honest-empty when the tenant
 * has no examples; a field the screenshot did not show renders "—", never a
 * fabricated value. Deep-linkable: navigate('memory', exampleId) selects one.
 */
import { useEffect, useRef } from 'react';
import { useData } from '@/lib/data/DataProvider';
import { useConsole } from '@/state/console-store';
import { useAsync } from '@/lib/useAsync';
import { ArtifactLibrary } from './studio/ArtifactLibrary';
import type { CampaignExample } from '@/lib/data/models';

function fmt(v: number | string | null | undefined): string {
  return v === null || v === undefined || v === '' ? '—' : String(v);
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ minWidth: 92 }}>
      <div className="label" style={{ fontSize: 10, color: 'var(--text-muted)' }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 600 }}>{value}</div>
    </div>
  );
}

function ExampleCard({ ex, highlighted }: { ex: CampaignExample; highlighted: boolean }) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (highlighted) ref.current?.scrollIntoView({ block: 'center' });
  }, [highlighted]);
  return (
    <section
      ref={ref}
      aria-label={`Campaign example ${ex.campaign_name}`}
      data-example-id={ex.id}
      style={{
        border: `1px solid ${highlighted ? 'var(--accent)' : 'var(--hairline)'}`,
        borderRadius: 10,
        background: 'var(--surface)',
        padding: 16,
        display: 'flex',
        gap: 16,
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
          <div style={{ fontSize: 15, fontWeight: 700 }}>{ex.campaign_name}</div>
          <span className="label" style={{ fontSize: 10.5, color: 'var(--text-muted)' }}>
            {fmt(ex.status)} · {fmt(ex.sent_at)}
          </span>
          <span
            className="label"
            style={{
              fontSize: 9.5, padding: '2px 6px', borderRadius: 'var(--radius-chip)',
              border: '1px solid var(--hairline)', color: 'var(--text-muted)',
            }}
            title="Transcribed from an operator-provided screenshot — never invented"
          >
            {fmt(ex.source)}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', marginTop: 10 }}>
          <Metric label="Artist" value={fmt(ex.artist_name)} />
          <Metric label="Offer" value={ex.offer_price_usd != null ? `$${ex.offer_price_usd}` : '—'} />
          <Metric label="Recipients" value={fmt(ex.recipient_count)} />
          <Metric label="Delivered" value={fmt(ex.delivered_count)} />
          <Metric label="Failed" value={fmt(ex.failed_count)} />
          <Metric label="DND blocked" value={fmt(ex.dnd_blocked_count)} />
          <Metric label="CTA" value={fmt(ex.cta)} />
        </div>
        {ex.message_copy ? (
          <pre
            style={{
              marginTop: 12, padding: 12, background: 'var(--surface-alt)',
              border: '1px solid var(--hairline)', borderRadius: 8,
              fontSize: 12, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere',
              maxHeight: 180, overflow: 'auto',
            }}
          >
            {ex.message_copy}
          </pre>
        ) : (
          <div style={{ marginTop: 12, fontSize: 12, color: 'var(--text-muted)' }}>
            message copy: missing (not visible in the source screenshot)
          </div>
        )}
      </div>
      <div style={{ width: 200, flexShrink: 0 }}>
        {ex.screenshot_url ? (
          /* eslint-disable-next-line @next/next/no-img-element -- local engine stream, no optimizer */
          <img
            src={ex.screenshot_url}
            alt={`Source screenshot for ${ex.campaign_name}`}
            style={{
              width: '100%', borderRadius: 8, border: '1px solid var(--hairline)',
            }}
          />
        ) : (
          <div
            style={{
              height: 120, display: 'flex', alignItems: 'center', justifyContent: 'center',
              border: '1px dashed var(--hairline-strong)', borderRadius: 8,
              fontSize: 11.5, color: 'var(--text-muted)', textAlign: 'center', padding: 8,
            }}
          >
            screenshot not present locally
          </div>
        )}
      </div>
    </section>
  );
}

export function MemoryScreen() {
  const { adapter, tenantId } = useData();
  const { contextId } = useConsole();
  const { data, loading, error } = useAsync(
    () => adapter.getCampaignExamples(tenantId),
    [adapter, tenantId],
  );

  const examples = data?.examples ?? [];
  const patterns = data?.patterns ?? [];
  return (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 14, overflow: 'auto', height: '100%' }}>
      {loading ? (
        <div style={{ color: 'var(--text-muted)' }}>Loading campaign memory…</div>
      ) : error ? (
        <div style={{ color: 'var(--danger-text)' }}>
          Failed to load campaign memory: {error.message}
        </div>
      ) : examples.length === 0 ? (
        <div style={{ color: 'var(--text-muted)' }}>
          No campaign examples ingested for this tenant yet — the library fills from
          real operator-provided screenshots (ju1.2), never fabricated examples.
        </div>
      ) : (
        <>
          {examples.map((ex) => (
            <ExampleCard key={ex.id} ex={ex} highlighted={ex.id === contextId} />
          ))}
          {patterns.length > 0 && (
            <section aria-label="Extracted patterns" style={{ marginTop: 8 }}>
              <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>
                Patterns extracted from these examples
              </div>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, color: 'var(--text-secondary)' }}>
                {patterns.map((p) => (
                  <li key={p.id} style={{ marginBottom: 4 }}>
                    <span className="mono" style={{ fontSize: 11 }}>{p.pattern_key}</span>
                    {p.description ? ` — ${p.description}` : ''}
                    <span style={{ color: 'var(--text-muted)' }}>
                      {' '}
                      (evidence: {p.evidence_example_ids.length} example
                      {p.evidence_example_ids.length === 1 ? '' : 's'})
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
      {/* Upload-center / knowledge view: EVERY context artifact the agents can see
          (GET /studio/artifacts) with kind filters + image previews. */}
      <ArtifactLibrary />
    </div>
  );
}
