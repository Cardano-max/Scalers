'use client';

/**
 * CustomerUpload — a "📎 Upload customers" control for the Campaign Studio.
 *
 * Picks a .csv, POSTs it to the studio backend's POST /studio/upload, and shows
 * an HONEST acknowledgement of what was parsed ("Uploaded N rows · columns: …").
 *
 * HONESTY: the backend does a REAL parse AND upserts the rows into the customers
 * table (keyed on tenant+email, idempotent) so the run's research tools can find
 * them. When ingestion lands the ack says how many leads were ingested; if the
 * backend reports it did NOT ingest (older backend / failure), the ack says so
 * instead of claiming otherwise. With no live endpoint (preview), it does not fake
 * a parse: it tells the operator the upload needs the live studio backend.
 */
import { useRef, useState } from 'react';

interface UploadAck {
  ok: boolean;
  filename?: string;
  rows?: number;
  columns?: string[];
  sample?: Array<Record<string, string>>;
  /** True only when the backend actually upserted the rows into `customers`. */
  ingested?: boolean;
  /** Real ingestion counts when `ingested` is true. */
  ingest?: { ingested?: number; created?: number; matched?: number };
  error?: string;
}

type Status = 'idle' | 'parsing' | 'done' | 'error';

/** Read a picked file as text. Uses FileReader (broad browser + jsdom support);
 *  falls back to File.text() where present. */
function readFileText(file: File): Promise<string> {
  if (typeof FileReader === 'undefined') return file.text();
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : '');
    reader.onerror = () => reject(reader.error ?? new Error('file read failed'));
    reader.readAsText(file);
  });
}

export function CustomerUpload({ endpoint, sessionId }: { endpoint?: string; sessionId?: string }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<Status>('idle');
  const [ack, setAck] = useState<UploadAck | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pick = () => inputRef.current?.click();

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    // reset the input so picking the same file again re-fires change.
    e.target.value = '';
    if (!file) return;

    setAck(null);
    setError(null);

    if (!endpoint) {
      setStatus('error');
      setError('Upload needs the live studio backend — not connected in preview.');
      return;
    }

    setStatus('parsing');
    try {
      const text = await readFileText(file);
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        // sessionId targets the operator's live studio session so the supervisor sees
        // THIS upload (not a stray default session). Omitted only in tests/preview.
        body: JSON.stringify({ filename: file.name, content: text, ...(sessionId ? { sessionId } : {}) }),
      });
      const data = (await res.json()) as UploadAck;
      if (!res.ok || !data.ok) {
        setStatus('error');
        setError(data.error || `upload failed (HTTP ${res.status})`);
        return;
      }
      setAck(data);
      setStatus('done');
    } catch (err) {
      setStatus('error');
      setError(err instanceof Error ? err.message : 'upload failed');
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
      <input
        ref={inputRef}
        type="file"
        accept=".csv,text/csv"
        onChange={onFile}
        style={{ display: 'none' }}
        data-testid="customer-csv-input"
      />
      <button
        type="button"
        onClick={pick}
        disabled={status === 'parsing'}
        aria-label="Upload customers CSV"
        style={{
          fontSize: 11.5,
          fontWeight: 600,
          padding: '4px 10px',
          border: '1px solid var(--hairline)',
          borderRadius: 999,
          background: '#fff',
          color: '#46423B',
          cursor: status === 'parsing' ? 'wait' : 'pointer',
          whiteSpace: 'nowrap',
        }}
      >
        {status === 'parsing' ? 'Parsing…' : '📎 Upload customers'}
      </button>

      {status === 'done' && ack && (
        <div
          role="status"
          style={{
            maxWidth: 320,
            textAlign: 'right',
            fontSize: 11,
            lineHeight: 1.4,
            color: '#157F4B',
          }}
        >
          Uploaded {ack.rows ?? 0} row{ack.rows === 1 ? '' : 's'}
          {ack.columns && ack.columns.length > 0 && (
            <> · columns: {ack.columns.join(', ')}</>
          )}
          <div style={{ color: '#8C877D' }}>
            {ack.ingested
              ? `Ingested ${ack.ingest?.ingested ?? ack.rows ?? 0} lead${
                  (ack.ingest?.ingested ?? ack.rows) === 1 ? '' : 's'
                } — the team can research them.`
              : 'Parsed only — not ingested yet.'}
          </div>
        </div>
      )}

      {status === 'error' && error && (
        <div role="alert" style={{ maxWidth: 320, textAlign: 'right', fontSize: 11, color: '#B42318' }}>
          {error}
        </div>
      )}
    </div>
  );
}
