'use client';

/**
 * KnowledgePanel — manage the PERSISTENT per-tenant document store for the studio.
 *
 * Upload a document (name + pasted text or a picked .txt/.md file), see the ACTIVE
 * documents the whole team is reading, and remove one (which drops it from every
 * agent surface at once). Backs onto the real engine routes:
 *   GET  /studio/documents          — list active docs
 *   POST /studio/documents          — { name, content, kind? } upload (persistent)
 *   POST /studio/documents/remove   — { id } soft-remove
 *
 * HONESTY: with no live endpoint (preview) it shows the honest not-connected note and
 * does not fake a store. A backend error is shown verbatim. Nothing here sends.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

interface DocRow {
  id: string;
  name: string;
  kind?: string | null;
  summary?: string | null;
  chars?: number | null;
  chunks?: number | null;
  source?: string | null;
}

function readFileText(file: File): Promise<string> {
  if (typeof FileReader === 'undefined') return file.text();
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : '');
    reader.onerror = () => reject(reader.error ?? new Error('file read failed'));
    reader.readAsText(file);
  });
}

export function KnowledgePanel({ endpoint }: { endpoint?: string }) {
  const [docs, setDocs] = useState<DocRow[]>([]);
  const [name, setName] = useState('');
  const [content, setContent] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    if (!endpoint) return;
    try {
      const res = await fetch(endpoint, { method: 'GET' });
      const data = (await res.json()) as { ok?: boolean; documents?: DocRow[]; error?: string };
      if (!res.ok || !data.ok) {
        setError(data.error || `list failed (HTTP ${res.status})`);
        return;
      }
      setDocs(data.documents ?? []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'list failed');
    } finally {
      setLoaded(true);
    }
  }, [endpoint]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    const text = await readFileText(file);
    setContent(text);
    if (!name.trim()) setName(file.name.replace(/\.(md|markdown|txt)$/i, ''));
  };

  const upload = async () => {
    if (!endpoint) {
      setError('Knowledge store needs the live studio backend — not connected in preview.');
      return;
    }
    if (!content.trim()) {
      setError('Paste or pick a document first.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name: name.trim() || 'Document', content }),
      });
      const data = (await res.json()) as { ok?: boolean; error?: string };
      if (!res.ok || !data.ok) {
        setError(data.error || `upload failed (HTTP ${res.status})`);
        return;
      }
      setName('');
      setContent('');
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'upload failed');
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    if (!endpoint) return;
    setBusy(true);
    try {
      const res = await fetch(`${endpoint}/remove`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ id }),
      });
      const data = (await res.json()) as { ok?: boolean; error?: string };
      if (!res.ok || !data.ok) {
        setError(data.error || `remove failed (HTTP ${res.status})`);
        return;
      }
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'remove failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      aria-label="Knowledge documents"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        border: '1px solid var(--hairline)',
        borderRadius: 12,
        background: '#fff',
        padding: 12,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontSize: 12.5, fontWeight: 600, color: '#46423B' }}>Knowledge</span>
        <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
          Documents the whole team reads
        </span>
      </div>

      {/* Active docs list */}
      {docs.length > 0 ? (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 6 }}>
          {docs.map((d) => (
            <li
              key={d.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                fontSize: 12,
                border: '1px solid var(--hairline)',
                borderRadius: 8,
                padding: '6px 8px',
              }}
            >
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ fontWeight: 600, color: '#46423B' }}>{d.name}</span>
                {d.summary ? (
                  <span
                    style={{
                      display: 'block',
                      color: '#8C877D',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {d.summary}
                  </span>
                ) : null}
              </span>
              <button
                type="button"
                onClick={() => remove(d.id)}
                disabled={busy}
                aria-label={`Remove ${d.name}`}
                title="Remove from every agent"
                style={{
                  fontSize: 11,
                  color: '#B42318',
                  background: 'transparent',
                  border: '1px solid var(--hairline)',
                  borderRadius: 6,
                  padding: '2px 8px',
                  cursor: busy ? 'wait' : 'pointer',
                }}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div style={{ fontSize: 11.5, color: '#8C877D' }}>
          {!endpoint
            ? 'Knowledge store needs the live studio backend.'
            : loaded
              ? 'No documents yet — add your brand playbook below.'
              : 'Loading…'}
        </div>
      )}

      {/* Add a document */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Document name (e.g. Brand Playbook)"
          aria-label="Document name"
          style={{
            fontSize: 12,
            padding: '5px 8px',
            border: '1px solid var(--hairline)',
            borderRadius: 8,
          }}
        />
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="Paste document text, or pick a file →"
          aria-label="Document text"
          rows={3}
          style={{
            fontSize: 12,
            padding: '6px 8px',
            border: '1px solid var(--hairline)',
            borderRadius: 8,
            resize: 'vertical',
          }}
        />
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input
            ref={fileRef}
            type="file"
            accept=".txt,.md,.markdown,text/plain"
            onChange={onFile}
            style={{ display: 'none' }}
            data-testid="knowledge-file-input"
          />
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            style={{
              fontSize: 11.5,
              padding: '4px 10px',
              border: '1px solid var(--hairline)',
              borderRadius: 999,
              background: '#fff',
              color: '#46423B',
              cursor: 'pointer',
            }}
          >
            Pick file
          </button>
          <button
            type="button"
            onClick={upload}
            disabled={busy}
            style={{
              fontSize: 11.5,
              fontWeight: 600,
              padding: '4px 12px',
              border: '1px solid var(--hairline)',
              borderRadius: 999,
              background: '#46423B',
              color: '#fff',
              cursor: busy ? 'wait' : 'pointer',
            }}
          >
            {busy ? 'Adding…' : 'Add document'}
          </button>
        </div>
      </div>

      {error ? (
        <div role="alert" style={{ fontSize: 11, color: '#B42318' }}>
          {error}
        </div>
      ) : null}
    </div>
  );
}
