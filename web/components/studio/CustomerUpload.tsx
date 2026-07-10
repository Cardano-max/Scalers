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
  /** The intake the backend routed this file to, by header shape:
   *  'conversations' | 'appointments' | 'competitors' | undefined (customers). */
  kind?: string;
  filename?: string;
  rows?: number;
  columns?: string[];
  sample?: Array<Record<string, string>>;
  /** True only when the backend actually upserted the rows into `customers`. */
  ingested?: boolean;
  /** Real ingestion counts when `ingested` is true. */
  ingest?: { ingested?: number; created?: number; matched?: number };
  // conversations intake
  customers?: number;
  conversations?: number;
  turns?: number;
  opted_out?: string[];
  // appointments intake
  appointments?: number;
  sessions?: number;
  // competitors intake
  handles?: string[];
  error?: string;
}

/** The honest per-intake acknowledgement. Each upload kind returns different
 *  counts — rendering them all through the customers shape showed
 *  "Uploaded 0 rows · not ingested" for a conversations file that had in fact
 *  fully ingested (a real operator hit this). Say what actually happened. */
function ackText(ack: UploadAck): { headline: string; detail: string } {
  if (ack.kind === 'conversations') {
    const opted = ack.opted_out?.length ?? 0;
    return {
      headline: `Imported ${ack.customers ?? 0} customer${(ack.customers ?? 0) === 1 ? '' : 's'} · ${ack.conversations ?? 0} conversation${(ack.conversations ?? 0) === 1 ? '' : 's'} · ${ack.turns ?? 0} messages`,
      detail:
        opted > 0
          ? `${opted} customer${opted === 1 ? '' : 's'} opted out of SMS — captured automatically.`
          : 'Verbatim threads stored — the team reads their exact words.',
    };
  }
  if (ack.kind === 'appointments') {
    return {
      headline: `Imported ${ack.appointments ?? 0} appointment${(ack.appointments ?? 0) === 1 ? '' : 's'} (${ack.sessions ?? 0} session day${(ack.sessions ?? 0) === 1 ? '' : 's'}) for ${ack.customers ?? 0} customer${(ack.customers ?? 0) === 1 ? '' : 's'}`,
      detail: 'Booking history is now on each customer’s record.',
    };
  }
  if (ack.kind === 'competitors') {
    return {
      headline: `Stored ${ack.rows ?? 0} competitor post${(ack.rows ?? 0) === 1 ? '' : 's'}${ack.handles?.length ? ` from ${ack.handles.join(', ')}` : ''}`,
      detail: 'For creative intelligence only — never send targets.',
    };
  }
  return {
    headline: `Uploaded ${ack.rows ?? 0} row${ack.rows === 1 ? '' : 's'}${ack.columns?.length ? ` · columns: ${ack.columns.join(', ')}` : ''}`,
    detail: ack.ingested
      ? `Ingested ${ack.ingest?.ingested ?? ack.rows ?? 0} lead${(ack.ingest?.ingested ?? ack.rows) === 1 ? '' : 's'} — the team can research them.`
      : 'Parsed only — not ingested yet.',
  };
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

export function CustomerUpload({
  endpoint,
  sessionId,
  onUploaded,
}: {
  endpoint?: string;
  sessionId?: string;
  /** Fires after a REAL successful upload — lets the page refresh context panels. */
  onUploaded?: () => void;
}) {
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
      onUploaded?.();
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
          {ackText(ack).headline}
          <div style={{ color: '#8C877D' }}>{ackText(ack).detail}</div>
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
