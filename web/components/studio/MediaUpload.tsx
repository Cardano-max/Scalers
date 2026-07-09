'use client';

/**
 * MediaUpload — one compact uploader for IMAGES *and* VIDEOS, mountable on any
 * surface (Voice, Agency, Memory). Posts to the same POST /studio/upload/image
 * route the Artists gallery uses; the engine dispatches videos to the
 * frame-sampled pipeline (real frames → VLM → merged tags → b-roll library row).
 *
 * HONESTY: the ack line is the engine's OWN response — a visual summary only
 * when analysis really ran; the explicit unavailable note otherwise. Optional
 * artist field links the piece to that artist's memory; left blank it lands in
 * the tenant library only.
 */
import { useRef, useState } from 'react';
import { uploadArtworkImage, type UploadImageResult } from '@/lib/studio/artists';

const ACCEPT =
  'image/png,image/jpeg,image/webp,video/mp4,video/quicktime,video/webm';
const ACCEPTED_TYPES = ACCEPT.split(',');
const TEAL = '#0F8A82';

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

export function MediaUpload({
  defaultArtist = '',
  onUploaded,
}: {
  defaultArtist?: string;
  onUploaded?: () => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [fileB64, setFileB64] = useState<string | null>(null);
  const [fileType, setFileType] = useState<string | null>(null);
  const [artist, setArtist] = useState(defaultArtist);
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<UploadImageResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setResult(null);
    setError(null);
    if (!ACCEPTED_TYPES.includes(file.type)) {
      setError(
        `Unsupported file type ${file.type || '(unknown)'} — use PNG/JPEG/WebP or MP4/MOV/WebM.`,
      );
      return;
    }
    try {
      const b64 = await readFileBase64(file);
      setFileName(file.name);
      setFileB64(b64);
      setFileType(file.type);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'file read failed');
    }
  };

  const upload = async () => {
    if (!fileB64 || !fileName || busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await uploadArtworkImage({
        name: fileName,
        contentBase64: fileB64,
        mediaType: fileType ?? undefined,
        artist: artist.trim(),
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
      onUploaded?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'upload failed');
    } finally {
      setBusy(false);
    }
  };

  const isVideo = (fileType ?? '').startsWith('video/');
  const ack = result
    ? result.vlmSummary
      ? `Visual analysis: ${result.vlmSummary}`
      : result.vlmStatus && !['ok', 'done', 'complete'].includes(result.vlmStatus.toLowerCase())
        ? 'Uploaded — visual analysis unavailable for this file (stored; tags pending).'
        : (result.note ?? 'Uploaded.')
    : null;

  return (
    <section
      aria-label="Upload image or video"
      style={{
        border: '1px solid var(--hairline)',
        borderRadius: 'var(--radius-card)',
        background: 'var(--surface)',
        padding: 14,
        display: 'grid',
        gap: 8,
      }}
    >
      <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--ink)' }}>
        Add image / video to memory
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          ref={fileRef}
          type="file"
          accept={ACCEPT}
          onChange={onFile}
          style={{ display: 'none' }}
          data-testid="media-file-input"
        />
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={busy}
          style={{
            font: 'inherit', fontSize: 12, fontWeight: 600, padding: '7px 12px',
            border: '1px solid var(--hairline)', borderRadius: 'var(--radius-button)',
            background: '#fff', color: 'var(--text-secondary)',
            cursor: busy ? 'wait' : 'pointer',
          }}
        >
          {fileName ? `Picked: ${fileName}${isVideo ? ' (video)' : ''}` : 'Pick image or video'}
        </button>
        <input
          type="text"
          value={artist}
          onChange={(e) => setArtist(e.target.value)}
          placeholder="Artist (optional)"
          style={{
            font: 'inherit', fontSize: 12, padding: '7px 10px', minWidth: 130,
            border: '1px solid var(--hairline)', borderRadius: 'var(--radius-button)',
          }}
        />
        <button
          type="button"
          onClick={upload}
          disabled={busy || !fileB64}
          style={{
            font: 'inherit', fontSize: 12, fontWeight: 600, padding: '7px 14px',
            border: 'none', borderRadius: 'var(--radius-button)', background: TEAL,
            color: '#fff', cursor: busy || !fileB64 ? 'not-allowed' : 'pointer',
            opacity: busy || !fileB64 ? 0.55 : 1,
          }}
        >
          {busy ? 'Uploading…' : 'Upload'}
        </button>
      </div>
      <input
        type="text"
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        placeholder="Notes about this piece (optional — stored with the memory)"
        style={{
          font: 'inherit', fontSize: 12, padding: '7px 10px',
          border: '1px solid var(--hairline)', borderRadius: 'var(--radius-button)',
        }}
      />
      {ack && <div style={{ fontSize: 12, color: TEAL }}>{ack}</div>}
      {error && <div style={{ fontSize: 12, color: '#B42318' }}>{error}</div>}
    </section>
  );
}
