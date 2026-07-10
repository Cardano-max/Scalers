'use client';

/**
 * ArtifactLibrary — the upload-center / knowledge view over the universal
 * context-artifact registry (GET /studio/artifacts): EVERYTHING the agents can
 * see for this tenant — docs, CSVs, images/artwork, screenshots — with kind
 * filters and real image previews (/studio/artifacts/{id}/raw).
 *
 * HONESTY: kinds offered as filters are exactly the kinds present in the real
 * list; items without a stored preview say so; a missing endpoint or transport
 * error renders the honest error state. Nothing here uploads or sends — upload
 * lives on the Voice composer (docs/CSV) and the Artist profile (artwork).
 */
import { useMemo, useState } from 'react';
import { useAsync } from '@/lib/useAsync';
import { Skeleton, EmptyState, ErrorState } from '../states';
import { Chip } from '../console-bits';
import { ArtifactMedia } from './ArtifactMedia';
import { artifactRawUrl, fetchArtifacts, type ContextArtifact } from '@/lib/studio/artists';

export function ArtifactLibrary() {
  const artifacts = useAsync(() => fetchArtifacts(), []);
  const [kind, setKind] = useState<string | null>(null);

  const items = useMemo(() => artifacts.data ?? [], [artifacts.data]);
  // The filter chips are EXACTLY the kinds that exist in the real registry.
  const kinds = useMemo(
    () => Array.from(new Set(items.map((a) => a.kind))).sort(),
    [items],
  );
  const filtered = kind ? items.filter((a) => a.kind === kind) : items;

  return (
    <section
      aria-label="Context artifacts"
      style={{
        border: '1px solid var(--hairline)',
        borderRadius: 'var(--radius-card)',
        background: 'var(--surface)',
        boxShadow: 'var(--shadow-card)',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          flexWrap: 'wrap',
          padding: '12px 16px',
          borderBottom: '1px solid var(--hairline-light, var(--hairline))',
        }}
      >
        <span style={{ fontWeight: 600, fontSize: 13 }}>Context artifacts</span>
        <span className="label" style={{ fontSize: 10 }}>
          everything the agents can see
        </span>
        <span style={{ flex: 1 }} />
        {kinds.map((k) => (
          <button
            key={k}
            type="button"
            onClick={() => setKind(kind === k ? null : k)}
            style={{
              font: 'inherit',
              fontSize: 11.5,
              fontWeight: kind === k ? 600 : 500,
              color: kind === k ? '#0B6F68' : 'var(--text-secondary)',
              background: kind === k ? 'var(--nav-active-bg)' : '#fff',
              border: `1px solid ${kind === k ? '#C9E5E1' : 'var(--hairline)'}`,
              padding: '4px 10px',
              borderRadius: 'var(--radius-pill)',
              cursor: 'pointer',
            }}
          >
            {k}
          </button>
        ))}
      </div>

      {artifacts.loading && artifacts.data === undefined ? (
        <Skeleton rows={3} label="Loading artifacts…" />
      ) : artifacts.error ? (
        <ErrorState error={artifacts.error} onRetry={artifacts.reload} />
      ) : items.length === 0 ? (
        <EmptyState
          title="No context artifacts yet"
          hint="Uploads (docs, CSVs, images, artwork) register here as agent-visible context — add them from the Voice composer or an artist profile."
        />
      ) : filtered.length === 0 ? (
        <EmptyState title={`No “${kind}” artifacts`} hint="Clear the filter to see everything." />
      ) : (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))',
            gap: 12,
            padding: 16,
          }}
        >
          {filtered.map((a) => (
            <ArtifactCard key={a.id} artifact={a} />
          ))}
        </div>
      )}
    </section>
  );
}

function ArtifactCard({ artifact }: { artifact: ContextArtifact }) {
  const vlmPending =
    artifact.vlmStatus && !['ok', 'done', 'complete'].includes(artifact.vlmStatus.toLowerCase());

  return (
    <article
      style={{
        border: '1px solid var(--hairline)',
        borderRadius: 10,
        overflow: 'hidden',
        background: '#fff',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {artifact.hasPreview ? (
        // Videos render as a real <video controls>, never a broken <img>.
        <ArtifactMedia src={artifactRawUrl(artifact.id)} alt={artifact.name} name={artifact.name} />
      ) : (
        <div
          style={{
            height: 110,
            display: 'grid',
            placeItems: 'center',
            background: 'var(--surface-alt)',
            color: 'var(--text-faint)',
            fontSize: 11,
            padding: 8,
            textAlign: 'center',
          }}
        >
          {artifact.kind} — no image preview
        </div>
      )}
      <div style={{ padding: '8px 10px', display: 'grid', gap: 5 }}>
        <div
          title={artifact.name}
          style={{
            fontSize: 12.5,
            fontWeight: 600,
            color: 'var(--ink)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {artifact.name}
        </div>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
          <Chip tone="teal" style={{ fontSize: 10 }}>
            {artifact.kind}
          </Chip>
          {artifact.artist && (
            <Chip tone="neutral" style={{ fontSize: 10 }}>
              {artifact.artist}
            </Chip>
          )}
          {vlmPending && (
            <span
              title="Visual analysis unavailable for this artifact"
              style={{ fontSize: 10, color: 'var(--text-faint)' }}
            >
              no visual analysis
            </span>
          )}
        </div>
        {artifact.createdAt && (
          <div className="mono" style={{ fontSize: 10, color: 'var(--text-muted)' }}>
            {artifact.createdAt.replace('T', ' ').slice(0, 16)}
          </div>
        )}
      </div>
    </article>
  );
}
