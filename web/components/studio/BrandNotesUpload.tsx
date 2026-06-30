'use client';

/**
 * BrandNotesUpload — a "Add brand / strategy notes" control for the studio.
 *
 * Picks a .txt / .md / .csv text file, POSTs its text to POST /studio/notes with
 * the session id, and shows an HONEST acknowledgement of what was attached. The
 * backend stores the text on the session's CampaignPlan.notes, so the Host reads
 * it on EVERY turn and the run loads it with the plan — it is REAL planning/run
 * context, not a badge.
 *
 * HONESTY: with no live endpoint (preview) it does not fake an attach — it tells
 * the operator the notes need the live studio backend. A backend error is shown
 * verbatim. Nothing is sent.
 */
import { useRef, useState } from 'react';

interface NotesAck {
  ok: boolean;
  filename?: string;
  chars?: number;
  error?: string;
}

type Status = 'idle' | 'attaching' | 'done' | 'error';

/** Read a picked file as text (FileReader for broad jsdom support; File.text fallback). */
function readFileText(file: File): Promise<string> {
  if (typeof FileReader === 'undefined') return file.text();
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : '');
    reader.onerror = () => reject(reader.error ?? new Error('file read failed'));
    reader.readAsText(file);
  });
}

export function BrandNotesUpload({
  endpoint,
  sessionId,
}: {
  /** POST /studio/notes endpoint; omit in preview to show the honest not-connected note. */
  endpoint?: string;
  sessionId: string;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<Status>('idle');
  const [ack, setAck] = useState<NotesAck | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pick = () => inputRef.current?.click();

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ''; // allow re-picking the same file
    if (!file) return;

    setAck(null);
    setError(null);

    if (!endpoint) {
      setStatus('error');
      setError('Notes need the live studio backend — not connected in preview.');
      return;
    }

    setStatus('attaching');
    try {
      const text = await readFileText(file);
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ sessionId, filename: file.name, content: text }),
      });
      const data = (await res.json()) as NotesAck;
      if (!res.ok || !data.ok) {
        setStatus('error');
        setError(data.error || `attach failed (HTTP ${res.status})`);
        return;
      }
      setAck(data);
      setStatus('done');
    } catch (err) {
      setStatus('error');
      setError(err instanceof Error ? err.message : 'attach failed');
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
      <input
        ref={inputRef}
        type="file"
        accept=".txt,.md,.markdown,.csv,text/plain"
        onChange={onFile}
        style={{ display: 'none' }}
        data-testid="brand-notes-input"
      />
      <button
        type="button"
        onClick={pick}
        disabled={status === 'attaching'}
        aria-label="Add brand or strategy notes"
        style={{
          fontSize: 11.5,
          fontWeight: 600,
          padding: '4px 10px',
          border: '1px solid var(--hairline)',
          borderRadius: 999,
          background: '#fff',
          color: '#46423B',
          cursor: status === 'attaching' ? 'wait' : 'pointer',
          whiteSpace: 'nowrap',
        }}
      >
        {status === 'attaching' ? 'Attaching…' : 'Add brand notes'}
      </button>

      {status === 'done' && ack && (
        <div
          role="status"
          style={{ maxWidth: 320, textAlign: 'right', fontSize: 11, lineHeight: 1.4, color: '#157F4B' }}
        >
          Brand notes attached ({ack.chars ?? 0} chars)
          <div style={{ color: '#8C877D' }}>The team reads these when planning and running.</div>
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
