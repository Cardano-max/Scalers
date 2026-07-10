'use client';

/**
 * BrandNotesUpload — the "Add brand notes" control for the studio.
 *
 * Clicking the button opens a SMALL modal with a textarea: the operator types
 * notes and they POST to /studio/notes with the session id — the exact same
 * payload path the .txt/.md file upload uses (the backend stores the text on the
 * session's CampaignPlan.notes, so the Host reads it on EVERY turn and the run
 * loads it with the plan). A file can still be attached from the same modal.
 *
 * HONESTY: with no live endpoint (preview) it does not fake an attach — it tells
 * the operator the notes need the live studio backend. A backend error is shown
 * verbatim. Nothing is sent to customers from here.
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
  onUploaded,
}: {
  /** POST /studio/notes endpoint; omit in preview to show the honest not-connected note. */
  endpoint?: string;
  sessionId: string;
  /** Fires after a REAL successful attach — lets the page refresh context panels. */
  onUploaded?: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<Status>('idle');
  const [ack, setAck] = useState<NotesAck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');

  /** The ONE payload path — both the typed notes and the picked file go through
   *  here, so notes always reach the campaign plan the same way. */
  const post = async (filename: string, content: string) => {
    setAck(null);
    setError(null);

    if (!endpoint) {
      setStatus('error');
      setError('Notes need the live studio backend — not connected in preview.');
      return;
    }

    setStatus('attaching');
    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ sessionId, filename, content }),
      });
      const data = (await res.json()) as NotesAck;
      if (!res.ok || !data.ok) {
        setStatus('error');
        setError(data.error || `attach failed (HTTP ${res.status})`);
        return;
      }
      setAck(data);
      setStatus('done');
      setOpen(false);
      setText('');
      onUploaded?.();
    } catch (err) {
      setStatus('error');
      setError(err instanceof Error ? err.message : 'attach failed');
    }
  };

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ''; // allow re-picking the same file
    if (!file) return;
    try {
      const content = await readFileText(file);
      await post(file.name, content);
    } catch (err) {
      setStatus('error');
      setError(err instanceof Error ? err.message : 'file read failed');
    }
  };

  const saveTyped = () => {
    const trimmed = text.trim();
    if (!trimmed || status === 'attaching') return;
    void post('brand-notes.txt', trimmed);
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
        onClick={() => setOpen(true)}
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
        {status === 'attaching' ? 'Saving…' : 'Add brand notes'}
      </button>

      {/* The small notes modal — type, save, done. A file works too. */}
      {open && (
        <>
          <div
            role="presentation"
            onClick={() => setOpen(false)}
            style={{
              position: 'fixed',
              inset: 0,
              background: 'rgba(26, 26, 23, 0.35)',
              zIndex: 999,
            }}
          />
          <div
            role="dialog"
            aria-label="Add brand notes"
            className="spring-in"
            style={{
              position: 'fixed',
              top: '50%',
              left: '50%',
              transform: 'translate(-50%, -50%)',
              width: 'min(460px, calc(100vw - 32px))',
              background: 'var(--surface)',
              border: '1px solid var(--hairline)',
              borderRadius: 'var(--radius-card)',
              boxShadow: 'var(--shadow-toast)',
              padding: 16,
              zIndex: 1000,
              display: 'grid',
              gap: 10,
              textAlign: 'left',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 14, fontWeight: 600 }}>Brand notes</span>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close"
                style={{
                  marginLeft: 'auto',
                  background: 'transparent',
                  border: 'none',
                  fontSize: 16,
                  color: 'var(--text-secondary)',
                  cursor: 'pointer',
                  padding: 0,
                }}
              >
                ✕
              </button>
            </div>
            <p style={{ margin: 0, fontSize: 12, lineHeight: 1.5, color: 'var(--text-muted)' }}>
              Anything the team should keep in mind — tone, offers, do&rsquo;s and
              don&rsquo;ts. The team reads this on every plan.
            </p>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={5}
              autoFocus
              placeholder="e.g. Warm and plain-spoken, no emoji. Push fine-line work this month…"
              aria-label="Brand notes text"
              style={{
                font: 'inherit',
                fontSize: 13,
                lineHeight: 1.5,
                padding: '9px 11px',
                border: '1px solid var(--hairline)',
                borderRadius: 9,
                resize: 'vertical',
                background: '#fff',
                color: 'var(--ink)',
              }}
            />
            {status === 'error' && error && (
              <div role="alert" style={{ fontSize: 11.5, color: '#B42318' }}>
                {error}
              </div>
            )}
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <button
                type="button"
                onClick={saveTyped}
                disabled={status === 'attaching' || text.trim().length === 0}
                style={{
                  font: 'inherit',
                  fontSize: 12.5,
                  fontWeight: 600,
                  padding: '8px 16px',
                  border: 'none',
                  borderRadius: 'var(--radius-button)',
                  background: 'var(--accent)',
                  color: '#fff',
                  cursor:
                    status === 'attaching' || text.trim().length === 0 ? 'not-allowed' : 'pointer',
                  opacity: status === 'attaching' || text.trim().length === 0 ? 0.55 : 1,
                }}
              >
                {status === 'attaching' ? 'Saving…' : 'Save notes'}
              </button>
              <button
                type="button"
                onClick={() => inputRef.current?.click()}
                disabled={status === 'attaching'}
                style={{
                  font: 'inherit',
                  fontSize: 12,
                  fontWeight: 500,
                  padding: '8px 12px',
                  border: '1px solid var(--hairline)',
                  borderRadius: 'var(--radius-button)',
                  background: '#fff',
                  color: 'var(--text-secondary)',
                  cursor: 'pointer',
                }}
              >
                …or attach a text file
              </button>
            </div>
          </div>
        </>
      )}

      {status === 'done' && ack && (
        <div
          role="status"
          style={{ maxWidth: 320, textAlign: 'right', fontSize: 11, lineHeight: 1.4, color: '#157F4B' }}
        >
          Saved to campaign context ({ack.chars ?? 0} chars)
          <div style={{ color: '#8C877D' }}>The team reads this on every plan.</div>
        </div>
      )}

      {status === 'error' && error && !open && (
        <div role="alert" style={{ maxWidth: 320, textAlign: 'right', fontSize: 11, color: '#B42318' }}>
          {error}
        </div>
      )}
    </div>
  );
}
