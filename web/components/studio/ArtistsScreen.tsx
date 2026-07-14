'use client';

/**
 * ArtistsScreen — the artist roster + per-artist profile (spec section 4/20).
 *
 * Roster: a card grid from GET /studio/artists (name, studios, real counts).
 * Profile: header (contact / studios / style tags), the artwork gallery (real
 * images via GET /studio/artifacts/{artifactId}/raw with styles/motifs chips and
 * the VLM summary on expand), the artist's REAL past campaigns (metrics + copy),
 * the memory timeline (+ add memory -> POST /studio/artists/{slug}/memory), and
 * an upload block that base64s an image and POSTs /studio/upload/image.
 *
 * HONESTY: every list renders a real empty state when the engine has nothing —
 * no placeholder artists, artworks, or campaigns. The Instagram / Google Drive
 * import buttons say plainly they are not connected in this environment. The
 * upload ack shows the engine's OWN response (VLM summary only when one really
 * came back). Nothing here sends anything.
 */
import { useCallback, useMemo, useRef, useState } from 'react';
import { useAsync } from '@/lib/useAsync';
import { Skeleton, EmptyState, ErrorState } from '../states';
import { Chip } from '../console-bits';
import { ArtifactMedia } from './ArtifactMedia';
import {
  addArtistMemory,
  artifactRawUrl,
  createArtist,
  fetchArtist,
  fetchArtists,
  uploadArtworkImage,
  type ArtistArtwork,
  type ArtistCampaign,
  type ArtistDetail,
  type ArtistMemory,
  type UploadImageResult,
} from '@/lib/studio/artists';

const TEAL = '#0F8A82';

const addFieldStyle: React.CSSProperties = {
  font: 'inherit',
  fontSize: 12.5,
  padding: '8px 10px',
  border: '1px solid var(--hairline)',
  borderRadius: 9,
  background: '#fff',
  color: 'var(--ink)',
};

export function ArtistsScreen() {
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);

  return selectedSlug ? (
    <ArtistProfile slug={selectedSlug} onBack={() => setSelectedSlug(null)} />
  ) : (
    <ArtistRoster onOpen={setSelectedSlug} />
  );
}

// ── Roster ───────────────────────────────────────────────────────────────────

/** Content-first roster order: artists with artwork/campaigns/memories sort
 *  before empty profiles; ties keep alphabetical order. */
function rosterSort(a: { artworkCount: number; campaignCount: number; memoryCount: number; name: string }, b: typeof a): number {
  const weight = (x: typeof a) => x.artworkCount * 100 + x.campaignCount * 10 + x.memoryCount;
  const d = weight(b) - weight(a);
  return d !== 0 ? d : a.name.localeCompare(b.name);
}

function ArtistRoster({ onOpen }: { onOpen: (slug: string) => void }) {
  const roster = useAsync(() => fetchArtists(), []);
  const [query, setQuery] = useState('');
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ name: '', studio: '', instagram: '', brandVoice: '' });
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const submitCreate = async () => {
    if (!form.name.trim() || createBusy) return;
    setCreateBusy(true);
    setCreateError(null);
    try {
      const res = await createArtist({
        name: form.name.trim(),
        studio: form.studio.trim() || undefined,
        instagram: form.instagram.trim() || undefined,
        brandVoice: form.brandVoice.trim() || undefined,
      });
      if (!res.ok || !res.slug) {
        setCreateError(res.error ?? 'create failed');
        return;
      }
      setAdding(false);
      setForm({ name: '', studio: '', instagram: '', brandVoice: '' });
      onOpen(res.slug); // straight into the new profile — upload artwork next
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'create failed');
    } finally {
      setCreateBusy(false);
    }
  };

  const visible = useMemo(() => {
    const all = [...(roster.data ?? [])].sort(rosterSort);
    const q = query.trim().toLowerCase();
    if (!q) return all;
    return all.filter(
      (a) =>
        a.name.toLowerCase().includes(q) ||
        a.studios.some((s) => s.toLowerCase().includes(q)),
    );
  }, [roster.data, query]);

  return (
    <div style={{ padding: 'var(--pad-section)', maxWidth: 1180, marginInline: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 14, flexWrap: 'wrap' }}>
        <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Artist roster</h2>
        <span className="label" style={{ fontSize: 10 }}>
          {roster.data ? `${roster.data.length} artist${roster.data.length === 1 ? '' : 's'}` : ''}
        </span>
        <button
          type="button"
          onClick={() => setAdding((v) => !v)}
          data-testid="add-artist-toggle"
          style={{
            font: 'inherit',
            fontSize: 12.5,
            fontWeight: 600,
            padding: '7px 14px',
            border: 'none',
            borderRadius: 'var(--radius-pill)',
            background: TEAL,
            color: '#fff',
            cursor: 'pointer',
          }}
        >
          {adding ? 'Close' : '＋ Add artist'}
        </button>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search artists or studios…"
          aria-label="Search artists"
          style={{
            marginLeft: 'auto',
            font: 'inherit',
            fontSize: 12.5,
            padding: '7px 12px',
            minWidth: 220,
            border: '1px solid var(--hairline)',
            borderRadius: 'var(--radius-pill)',
            background: 'var(--surface)',
            color: 'var(--ink)',
          }}
        />
      </div>

      {adding && (
        <section
          aria-label="Add artist"
          style={{
            border: '1px solid var(--hairline)',
            borderRadius: 'var(--radius-card)',
            background: 'var(--surface)',
            boxShadow: 'var(--shadow-card)',
            padding: 16,
            marginBottom: 16,
            display: 'grid',
            gap: 10,
          }}
        >
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 10 }}>
            <input
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="Artist name (required)"
              aria-label="Artist name"
              data-testid="add-artist-name"
              style={addFieldStyle}
            />
            <input
              value={form.studio}
              onChange={(e) => setForm((f) => ({ ...f, studio: e.target.value }))}
              placeholder="Studio (e.g. Skin Design Tattoos)"
              aria-label="Studio"
              style={addFieldStyle}
            />
            <input
              value={form.instagram}
              onChange={(e) => setForm((f) => ({ ...f, instagram: e.target.value }))}
              placeholder="Instagram (@handle, optional)"
              aria-label="Instagram handle"
              style={addFieldStyle}
            />
          </div>
          <textarea
            value={form.brandVoice}
            onChange={(e) => setForm((f) => ({ ...f, brandVoice: e.target.value }))}
            rows={2}
            placeholder="Brand voice notes (optional) — e.g. warm, direct, never discount-led. Stored as artist memory; the drafting team reads it."
            aria-label="Brand voice notes"
            style={{ ...addFieldStyle, resize: 'vertical' }}
          />
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button
              type="button"
              onClick={submitCreate}
              disabled={createBusy || !form.name.trim()}
              data-testid="add-artist-submit"
              style={{
                font: 'inherit',
                fontSize: 12.5,
                fontWeight: 600,
                padding: '8px 16px',
                border: 'none',
                borderRadius: 'var(--radius-button)',
                background: TEAL,
                color: '#fff',
                cursor: createBusy || !form.name.trim() ? 'not-allowed' : 'pointer',
                opacity: createBusy || !form.name.trim() ? 0.55 : 1,
              }}
            >
              {createBusy ? 'Creating…' : 'Create artist'}
            </button>
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              Opens the profile next — upload artwork there (multi-select supported).
            </span>
          </div>
          {createError && (
            <div role="alert" style={{ fontSize: 12.5, color: 'var(--danger-text)' }}>
              {createError}
            </div>
          )}
        </section>
      )}

      {roster.loading && roster.data === undefined ? (
        <Skeleton rows={4} label="Loading artists…" />
      ) : roster.error ? (
        <ErrorState error={roster.error} onRetry={roster.reload} />
      ) : !roster.data || roster.data.length === 0 ? (
        <EmptyState
          title="No artists yet"
          hint="The engine has no artist profiles for this tenant. They appear here once artist data is ingested."
        />
      ) : visible.length === 0 ? (
        <EmptyState
          title={`No artists match “${query.trim()}”`}
          hint="Try a shorter name, or clear the search."
        />
      ) : (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))',
            gap: 14,
          }}
        >
          {visible.map((a) => (
            <button
              key={a.slug}
              type="button"
              onClick={() => onOpen(a.slug)}
              aria-label={`Open ${a.name}`}
              style={{
                textAlign: 'left',
                font: 'inherit',
                cursor: 'pointer',
                display: 'flex',
                flexDirection: 'column',
                gap: 10,
                padding: 16,
                border: '1px solid var(--hairline)',
                borderRadius: 'var(--radius-card)',
                background: 'var(--surface)',
                boxShadow: 'var(--shadow-card)',
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLElement).style.borderColor = TEAL;
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.borderColor = 'var(--hairline)';
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span
                  aria-hidden
                  style={{
                    width: 36,
                    height: 36,
                    borderRadius: '50%',
                    display: 'grid',
                    placeItems: 'center',
                    background: 'var(--nav-active-bg)',
                    color: TEAL,
                    fontWeight: 700,
                    fontSize: 14,
                    flex: '0 0 auto',
                  }}
                >
                  {initials(a.name)}
                </span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 14.5, fontWeight: 600, color: 'var(--ink)' }}>{a.name}</div>
                  <div
                    style={{
                      fontSize: 11.5,
                      color: 'var(--text-muted)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {a.studios.length > 0 ? a.studios.join(' · ') : 'No studio on record'}
                  </div>
                </div>
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <Chip tone={a.artworkCount > 0 ? 'teal' : 'neutral'}>
                  {a.artworkCount} artwork{a.artworkCount === 1 ? '' : 's'}
                </Chip>
                <Chip tone="neutral">{a.campaignCount} campaign{a.campaignCount === 1 ? '' : 's'}</Chip>
                <Chip tone="neutral">{a.memoryCount} memor{a.memoryCount === 1 ? 'y' : 'ies'}</Chip>
              </div>
              {a.artworkCount === 0 && a.campaignCount === 0 && a.memoryCount === 0 && (
                <span style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
                  No artwork yet — upload from the Voice tab, or open this profile to add some.
                </span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Profile ──────────────────────────────────────────────────────────────────

function ArtistProfile({ slug, onBack }: { slug: string; onBack: () => void }) {
  const artist = useAsync(() => fetchArtist(slug), [slug]);

  return (
    <div style={{ padding: 'var(--pad-section)', maxWidth: 1180, marginInline: 'auto', display: 'grid', gap: 18 }}>
      <div>
        <button
          type="button"
          onClick={onBack}
          style={{
            font: 'inherit',
            fontSize: 12.5,
            fontWeight: 600,
            color: TEAL,
            background: 'transparent',
            border: 'none',
            padding: 0,
            cursor: 'pointer',
          }}
        >
          ← All artists
        </button>
      </div>

      {artist.loading && artist.data === undefined ? (
        <Skeleton rows={5} label="Loading artist…" />
      ) : artist.error ? (
        <ErrorState error={artist.error} onRetry={artist.reload} />
      ) : artist.data ? (
        <ArtistProfileBody artist={artist.data} onMemoryAdded={artist.reload} onArtworkUploaded={artist.reload} />
      ) : (
        <EmptyState title="Artist not found" hint={`No profile for “${slug}”.`} />
      )}
    </div>
  );
}

function ArtistProfileBody({
  artist,
  onMemoryAdded,
  onArtworkUploaded,
}: {
  artist: ArtistDetail;
  onMemoryAdded: () => void;
  onArtworkUploaded: () => void;
}) {
  const memories = useMemo(
    () => [...artist.memories].sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0)),
    [artist.memories],
  );

  return (
    <>
      {/* Header */}
      <section
        aria-label="Artist header"
        style={{
          border: '1px solid var(--hairline)',
          borderRadius: 'var(--radius-card)',
          background: 'var(--surface)',
          boxShadow: 'var(--shadow-card)',
          padding: 18,
          display: 'flex',
          gap: 16,
          alignItems: 'flex-start',
          flexWrap: 'wrap',
        }}
      >
        <span
          aria-hidden
          style={{
            width: 52,
            height: 52,
            borderRadius: '50%',
            display: 'grid',
            placeItems: 'center',
            background: 'var(--nav-active-bg)',
            color: TEAL,
            fontWeight: 700,
            fontSize: 19,
            flex: '0 0 auto',
          }}
        >
          {initials(artist.name)}
        </span>
        <div style={{ minWidth: 220, flex: 1 }}>
          <h2 style={{ margin: 0, fontSize: 19, fontWeight: 650, letterSpacing: '-0.01em' }}>{artist.name}</h2>
          <div style={{ marginTop: 4, fontSize: 12.5, color: 'var(--text-muted)', display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <span>{artist.email ?? 'no email on record'}</span>
            <span style={{ color: 'var(--text-faint)' }}>·</span>
            <span>{artist.phone ?? 'no phone on record'}</span>
          </div>
          <div style={{ marginTop: 10, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {artist.studios.map((s) => (
              <Chip key={s} tone="neutral">
                {s}
              </Chip>
            ))}
            {artist.styleTags.map((t) => (
              <Chip key={t} tone="teal">
                {t}
              </Chip>
            ))}
            {artist.studios.length === 0 && artist.styleTags.length === 0 && (
              <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>No studios or style tags on record</span>
            )}
          </div>
        </div>
      </section>

      {/* Artwork gallery */}
      <ArtworkGallery artworks={artist.artworks} />

      {/* Upload artwork */}
      <UploadArtworkBlock artistSlug={artist.slug} onUploaded={onArtworkUploaded} />

      {/* Old campaigns */}
      <CampaignHistory campaigns={artist.campaigns} />

      {/* Memory timeline */}
      <MemoryTimeline slug={artist.slug} memories={memories} onAdded={onMemoryAdded} />
    </>
  );
}

// ── Artwork gallery ──────────────────────────────────────────────────────────

function ArtworkGallery({ artworks }: { artworks: ArtistArtwork[] }) {
  return (
    <section
      aria-label="Artwork gallery"
      style={{
        border: '1px solid var(--hairline)',
        borderRadius: 'var(--radius-card)',
        background: 'var(--surface)',
        boxShadow: 'var(--shadow-card)',
      }}
    >
      <PanelHeader title="Artwork" count={artworks.length} />
      {artworks.length === 0 ? (
        <div style={{ padding: '18px 16px', fontSize: 13, color: 'var(--text-muted)' }}>
          No artwork uploaded yet — upload below or provide a Drive/Instagram source.
        </div>
      ) : (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
            gap: 12,
            padding: 16,
          }}
        >
          {artworks.map((w) => (
            <ArtworkCard key={w.assetId || w.artifactId} artwork={w} />
          ))}
        </div>
      )}
    </section>
  );
}

function ArtworkCard({ artwork }: { artwork: ArtistArtwork }) {
  const [expanded, setExpanded] = useState(false);
  const chips = [...artwork.styles, ...artwork.motifs];

  return (
    <figure
      style={{
        margin: 0,
        border: '1px solid var(--hairline)',
        borderRadius: 10,
        overflow: 'hidden',
        background: '#fff',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Videos render as a playable <video controls>; images stay images. The
          media sits OUTSIDE the expand button so video controls stay clickable. */}
      <ArtifactMedia
        src={artifactRawUrl(artwork.artifactId)}
        alt={artwork.vlmSummary ?? 'artwork'}
        height={140}
      />
      <figcaption style={{ padding: '8px 10px', display: 'grid', gap: 6 }}>
        {chips.length > 0 ? (
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            {artwork.styles.map((s) => (
              <Chip key={`s_${s}`} tone="teal" style={{ fontSize: 10 }}>
                {s}
              </Chip>
            ))}
            {artwork.motifs.map((m) => (
              <Chip key={`m_${m}`} tone="neutral" style={{ fontSize: 10 }}>
                {m}
              </Chip>
            ))}
          </div>
        ) : (
          <span style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>no style/motif tags</span>
        )}
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          aria-expanded={expanded}
          style={{
            font: 'inherit',
            fontSize: 10.5,
            fontWeight: 600,
            color: TEAL,
            background: 'transparent',
            border: 'none',
            padding: 0,
            cursor: 'pointer',
            textAlign: 'left',
          }}
        >
          {expanded ? 'Hide description ▲' : 'What is this piece? ▾'}
        </button>
        {expanded && (
          <p style={{ margin: 0, fontSize: 11.5, lineHeight: 1.5, color: 'var(--text-secondary)' }}>
            {artwork.vlmSummary ??
              (artwork.vlmError
                ? `Visual analysis was skipped at upload: ${artwork.vlmError} — fix the engine and re-upload this piece to analyze it.`
                : 'No visual analysis available for this artwork.')}
          </p>
        )}
      </figcaption>
    </figure>
  );
}

// ── Upload artwork ───────────────────────────────────────────────────────────

const ACCEPTED_IMAGE_TYPES = [
  'image/png',
  'image/jpeg',
  'image/webp',
  // Videos ride the same upload route; the engine samples real frames for the
  // visual analysis and stores the piece as a b-roll candidate.
  'video/mp4',
  'video/quicktime',
  'video/webm',
];

/** Read a picked file as base64 (payload only, no data-uri prefix). */
function readFileBase64(file: File): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === 'string' ? reader.result : '';
      const idx = result.indexOf(',');
      resolve(idx >= 0 ? result.slice(idx + 1) : result);
    };
    reader.onerror = () => reject(reader.error ?? new Error('file read failed'));
    reader.readAsDataURL(file);
  });
}

function UploadArtworkBlock({
  artistSlug,
  onUploaded,
}: {
  artistSlug: string;
  onUploaded: () => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [fileB64, setFileB64] = useState<string | null>(null);
  const [fileType, setFileType] = useState<string | null>(null);
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<UploadImageResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [importNote, setImportNote] = useState<string | null>(null);

  const [queue, setQueue] = useState<File[]>([]);
  const [bulk, setBulk] = useState<{ done: number; total: number; failed: string[] } | null>(null);

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = '';
    if (!files.length) return;
    setResult(null);
    setError(null);
    setBulk(null);
    const bad = files.filter((f) => !ACCEPTED_IMAGE_TYPES.includes(f.type));
    if (bad.length) {
      setError(`Unsupported file type in selection (${bad[0].name}) — use PNG/JPEG/WebP or MP4/MOV/WebM.`);
      return;
    }
    if (files.length > 1) {
      // BULK: many pieces at once (the "50 pictures" flow) — queued, uploaded
      // one-by-one so every image gets its own real VLM analysis.
      setQueue(files);
      setFileName(null);
      setFileB64(null);
      setFileType(null);
      return;
    }
    setQueue([]);
    try {
      const b64 = await readFileBase64(files[0]);
      setFileName(files[0].name);
      setFileB64(b64);
      setFileType(files[0].type);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'file read failed');
    }
  };

  const upload = async () => {
    if (busy) return;
    // BULK path: sequential uploads with live progress; failures collected, not
    // silently dropped — the operator sees exactly which files need a retry.
    if (queue.length > 0) {
      setBusy(true);
      setError(null);
      setResult(null);
      const failed: string[] = [];
      const total = queue.length;
      setBulk({ done: 0, total, failed });
      for (let i = 0; i < total; i += 1) {
        const file = queue[i];
        try {
          const b64 = await readFileBase64(file);
          const res = await uploadArtworkImage({
            name: file.name,
            contentBase64: b64,
            mediaType: file.type || undefined,
            artist: artistSlug,
            prompt: prompt.trim() || undefined,
          });
          if (!res.ok) failed.push(`${file.name}: ${res.error ?? 'upload failed'}`);
        } catch (err) {
          failed.push(`${file.name}: ${err instanceof Error ? err.message : 'failed'}`);
        }
        setBulk({ done: i + 1, total, failed: [...failed] });
      }
      setQueue([]);
      setPrompt('');
      setBusy(false);
      onUploaded();
      return;
    }
    if (!fileB64 || !fileName) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await uploadArtworkImage({
        name: fileName,
        contentBase64: fileB64,
        mediaType: fileType ?? undefined,
        artist: artistSlug,
        prompt: prompt.trim() || undefined,
      });
      if (!res.ok) {
        setError(res.error ?? 'upload failed');
        return;
      }
      setResult(res);
      setFileName(null);
      setFileB64(null);
      setFileType(null);
      setPrompt('');
      onUploaded();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'upload failed');
    } finally {
      setBusy(false);
    }
  };

  // The engine's honest ack: a VLM summary ONLY when one really came back;
  // otherwise the explicit "visual analysis unavailable" state / the engine note.
  const ackLine = result
    ? result.vlmSummary
      ? `Visual analysis: ${result.vlmSummary}`
      : result.vlmStatus && !['ok', 'done', 'complete'].includes(result.vlmStatus.toLowerCase())
        ? 'Uploaded — visual analysis unavailable for this file.'
        : result.note ?? 'Uploaded.'
    : null;

  return (
    <section
      aria-label="Upload artwork"
      style={{
        border: '1px solid var(--hairline)',
        borderRadius: 'var(--radius-card)',
        background: 'var(--surface)',
        boxShadow: 'var(--shadow-card)',
      }}
    >
      <PanelHeader title="Upload artwork" />
      <div style={{ padding: 16, display: 'grid', gap: 10 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <input
            ref={fileRef}
            type="file"
            multiple
            accept="image/png,image/jpeg,image/webp,video/mp4,video/quicktime,video/webm"
            onChange={onFile}
            style={{ display: 'none' }}
            data-testid="artwork-file-input"
          />
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={busy}
            style={{
              font: 'inherit',
              fontSize: 12.5,
              fontWeight: 600,
              padding: '8px 14px',
              border: '1px solid var(--hairline)',
              borderRadius: 'var(--radius-button)',
              background: '#fff',
              color: 'var(--text-secondary)',
              cursor: busy ? 'wait' : 'pointer',
            }}
          >
            {queue.length > 1
              ? `Picked: ${queue.length} files`
              : fileName
                ? `Picked: ${fileName}`
                : 'Pick images or videos (multi-select)'}
          </button>
          <button
            type="button"
            onClick={upload}
            disabled={busy || (!fileB64 && queue.length === 0)}
            style={{
              font: 'inherit',
              fontSize: 12.5,
              fontWeight: 600,
              padding: '8px 16px',
              border: 'none',
              borderRadius: 'var(--radius-button)',
              background: TEAL,
              color: '#fff',
              cursor: busy || (!fileB64 && queue.length === 0) ? 'not-allowed' : 'pointer',
              opacity: busy || (!fileB64 && queue.length === 0) ? 0.55 : 1,
            }}
          >
            {busy && bulk
              ? `Uploading ${bulk.done}/${bulk.total}…`
              : busy
                ? 'Uploading…'
                : queue.length > 1
                  ? `Upload ${queue.length} artworks`
                  : 'Upload artwork'}
          </button>
          <span style={{ flex: 1 }} />
          {/* Honest import stubs — NOT connected; no fake integration. */}
          <button
            type="button"
            title="Instagram is not connected in this environment — upload manually."
            onClick={() => setImportNote('Instagram is not connected in this environment — upload manually.')}
            style={importBtnStyle}
          >
            Import from Instagram
          </button>
          <button
            type="button"
            title="Google Drive is not connected in this environment — upload manually."
            onClick={() => setImportNote('Google Drive is not connected in this environment — upload manually.')}
            style={importBtnStyle}
          >
            Import from Google Drive
          </button>
        </div>

        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={2}
          placeholder="Optional — what is this design / when should the team use it?"
          aria-label="Artwork context prompt"
          style={{
            font: 'inherit',
            fontSize: 12.5,
            lineHeight: 1.5,
            padding: '8px 10px',
            border: '1px solid var(--hairline)',
            borderRadius: 9,
            resize: 'vertical',
            background: '#fff',
            color: 'var(--ink)',
          }}
        />

        {importNote && (
          <div role="note" style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            {importNote}
          </div>
        )}
        {ackLine && (
          <div role="status" style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--success-text, #157F4B)' }}>
            {ackLine}
          </div>
        )}
        {bulk && (
          <div role="status" style={{ fontSize: 12.5, lineHeight: 1.6 }}>
            <span style={{ color: 'var(--success-text, #157F4B)' }}>
              {busy
                ? `Uploading + tagging ${bulk.done}/${bulk.total}…`
                : `${bulk.total - bulk.failed.length}/${bulk.total} uploaded and visually analyzed.`}
            </span>
            {!busy && bulk.failed.length > 0 && (
              <div role="alert" style={{ color: 'var(--danger-text)', marginTop: 4 }}>
                {bulk.failed.length} failed — pick just these and retry:
                {bulk.failed.slice(0, 5).map((f) => (
                  <div key={f} style={{ fontSize: 12 }}>{f}</div>
                ))}
              </div>
            )}
          </div>
        )}
        {error && (
          <div role="alert" style={{ fontSize: 12.5, color: 'var(--danger-text)' }}>
            {error}
          </div>
        )}
      </div>
    </section>
  );
}

const importBtnStyle: React.CSSProperties = {
  font: 'inherit',
  fontSize: 12,
  fontWeight: 500,
  padding: '7px 12px',
  border: '1px dashed var(--hairline-strong, var(--hairline))',
  borderRadius: 'var(--radius-button)',
  background: 'transparent',
  color: 'var(--text-muted)',
  cursor: 'help',
};

// ── Campaign history ─────────────────────────────────────────────────────────

function CampaignHistory({ campaigns }: { campaigns: ArtistCampaign[] }) {
  return (
    <section
      aria-label="Past campaigns"
      style={{
        border: '1px solid var(--hairline)',
        borderRadius: 'var(--radius-card)',
        background: 'var(--surface)',
        boxShadow: 'var(--shadow-card)',
      }}
    >
      <PanelHeader title="Past campaigns" count={campaigns.length} />
      {campaigns.length === 0 ? (
        <div style={{ padding: '18px 16px', fontSize: 13, color: 'var(--text-muted)' }}>
          No past campaigns on record for this artist.
        </div>
      ) : (
        <div style={{ padding: 16, display: 'grid', gap: 12 }}>
          {campaigns.map((c, i) => (
            <CampaignCard key={`${c.campaign_name}_${i}`} campaign={c} />
          ))}
        </div>
      )}
    </section>
  );
}

function CampaignCard({ campaign }: { campaign: ArtistCampaign }) {
  const [copyOpen, setCopyOpen] = useState(false);
  return (
    <article
      style={{
        border: '1px solid var(--hairline)',
        borderRadius: 10,
        padding: '12px 14px',
        background: '#fff',
        display: 'grid',
        gap: 8,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--ink)' }}>{campaign.campaign_name}</span>
        {campaign.offer_price_usd != null && (
          <span className="mono" style={{ fontSize: 12, color: TEAL, fontWeight: 600 }}>
            ${campaign.offer_price_usd}
          </span>
        )}
        <span style={{ flex: 1 }} />
        {campaign.sent_at && (
          <span className="mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            {campaign.sent_at.slice(0, 10)}
          </span>
        )}
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <Chip tone="success">{campaign.delivered_count ?? 0} delivered</Chip>
        <Chip tone={(campaign.failed_count ?? 0) > 0 ? 'danger' : 'neutral'}>
          {campaign.failed_count ?? 0} failed
        </Chip>
        <Chip tone={(campaign.dnd_blocked_count ?? 0) > 0 ? 'amber' : 'neutral'}>
          {campaign.dnd_blocked_count ?? 0} DND-blocked
        </Chip>
        {campaign.cta && <Chip tone="teal">CTA: {campaign.cta}</Chip>}
      </div>
      {campaign.message_copy ? (
        <div>
          <button
            type="button"
            onClick={() => setCopyOpen((o) => !o)}
            aria-expanded={copyOpen}
            style={{
              font: 'inherit',
              fontSize: 11.5,
              fontWeight: 600,
              color: TEAL,
              background: 'transparent',
              border: 'none',
              padding: 0,
              cursor: 'pointer',
            }}
          >
            {copyOpen ? 'Hide message copy ▲' : 'Show message copy ▾'}
          </button>
          {copyOpen && (
            <pre
              style={{
                margin: '8px 0 0',
                font: 'inherit',
                fontSize: 12.5,
                lineHeight: 1.55,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                color: 'var(--text-secondary)',
                background: 'var(--surface-alt)',
                border: '1px solid var(--hairline)',
                borderRadius: 8,
                padding: '10px 12px',
              }}
            >
              {campaign.message_copy}
            </pre>
          )}
        </div>
      ) : (
        <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>message copy not transcribed</span>
      )}
    </article>
  );
}

// ── Memory timeline ──────────────────────────────────────────────────────────

function MemoryTimeline({
  slug,
  memories,
  onAdded,
}: {
  slug: string;
  memories: ArtistMemory[];
  onAdded: () => void;
}) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const save = useCallback(async () => {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await addArtistMemory(slug, trimmed);
      setText('');
      setSaved(true);
      onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'memory save failed');
    } finally {
      setBusy(false);
    }
  }, [slug, text, busy, onAdded]);

  return (
    <section
      aria-label="Memory timeline"
      style={{
        border: '1px solid var(--hairline)',
        borderRadius: 'var(--radius-card)',
        background: 'var(--surface)',
        boxShadow: 'var(--shadow-card)',
      }}
    >
      <PanelHeader title="Memory" count={memories.length} />
      <div style={{ padding: 16, display: 'grid', gap: 12 }}>
        {/* Add memory */}
        <div style={{ display: 'grid', gap: 8 }}>
          <textarea
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setSaved(false);
            }}
            rows={2}
            placeholder="Add a memory the team should keep — preferences, booking notes, do/don't…"
            aria-label="Add memory"
            style={{
              font: 'inherit',
              fontSize: 12.5,
              lineHeight: 1.5,
              padding: '8px 10px',
              border: '1px solid var(--hairline)',
              borderRadius: 9,
              resize: 'vertical',
              background: '#fff',
              color: 'var(--ink)',
            }}
          />
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <button
              type="button"
              onClick={save}
              disabled={busy || text.trim().length === 0}
              style={{
                font: 'inherit',
                fontSize: 12.5,
                fontWeight: 600,
                padding: '7px 14px',
                border: 'none',
                borderRadius: 'var(--radius-button)',
                background: TEAL,
                color: '#fff',
                cursor: busy || text.trim().length === 0 ? 'not-allowed' : 'pointer',
                opacity: busy || text.trim().length === 0 ? 0.55 : 1,
              }}
            >
              {busy ? 'Saving…' : 'Add memory'}
            </button>
            {saved && (
              <span role="status" style={{ fontSize: 12, color: 'var(--success-text, #157F4B)' }}>
                Memory saved.
              </span>
            )}
            {error && (
              <span role="alert" style={{ fontSize: 12, color: 'var(--danger-text)' }}>
                {error}
              </span>
            )}
          </div>
        </div>

        {/* Timeline — newest first */}
        {memories.length === 0 ? (
          <div style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>
            No memories yet — the first note you add appears here.
          </div>
        ) : (
          <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 0 }}>
            {memories.map((m, i) => (
              <li key={`${m.at}_${i}`} style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', alignSelf: 'stretch' }}>
                  <span
                    aria-hidden
                    style={{ width: 8, height: 8, borderRadius: '50%', background: TEAL, marginTop: 5, flex: '0 0 auto' }}
                  />
                  {i < memories.length - 1 && (
                    <span aria-hidden style={{ flex: 1, width: 1, background: 'var(--hairline)', margin: '4px 0' }} />
                  )}
                </div>
                <div style={{ paddingBottom: 14, minWidth: 0 }}>
                  <div className="mono" style={{ fontSize: 10.5, color: 'var(--text-muted)' }}>
                    {m.at ? m.at.replace('T', ' ').slice(0, 16) : 'undated'}
                  </div>
                  <div style={{ fontSize: 13, lineHeight: 1.5, color: 'var(--text-secondary)', wordBreak: 'break-word' }}>
                    {m.text}
                  </div>
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}

// ── bits ─────────────────────────────────────────────────────────────────────

function PanelHeader({ title, count }: { title: string; count?: number }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '12px 16px',
        borderBottom: '1px solid var(--hairline-light, var(--hairline))',
      }}
    >
      <span style={{ fontWeight: 600, fontSize: 13 }}>{title}</span>
      {typeof count === 'number' && (
        <span className="label" style={{ marginLeft: 'auto', fontSize: 10 }}>
          {count}
        </span>
      )}
    </div>
  );
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}
