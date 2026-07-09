'use client';

/**
 * KnowledgePanel — manage the PERSISTENT per-tenant document store for the studio.
 *
 * Upload a document (name + pasted text or a picked .txt/.md/.csv file), see the
 * ACTIVE documents the whole team is reading, and remove one (which drops it from
 * every agent surface at once). A picked .csv is sent with kind="csv" so the engine
 * chunks it one retrievable passage per row; .txt/.md and pasted text are kind="doc".
 * Backs onto the real engine routes:
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

interface KnowledgePanelProps {
  endpoint?: string;
  /** Render as a collapsed-by-default summary that expands to the full panel. */
  collapsible?: boolean;
  /** Initial open state when collapsible (defaults closed). Ignored when not collapsible. */
  defaultOpen?: boolean;
  /** Bump to force a re-fetch of the document list (e.g. after an upload succeeds
   *  elsewhere on the page) so the summary never shows a stale "No documents yet". */
  refreshToken?: number;
}

export function KnowledgePanel({ endpoint, collapsible = false, defaultOpen = false, refreshToken = 0 }: KnowledgePanelProps) {
  const [docs, setDocs] = useState<DocRow[]>([]);
  const [name, setName] = useState('');
  const [content, setContent] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  // Collapsible mode (Voice page): start collapsed so the live transcript stays the
  // hero and this never grows into a page-dominating card. Non-collapsible callers
  // (e.g. a dedicated knowledge route) always render the full body.
  const [open, setOpen] = useState(collapsible ? defaultOpen : true);
  // Document kind sent to the store: a picked .csv becomes a CSV doc (chunked per
  // row); .txt/.md and pasted text are plain docs. Reset to 'doc' on manual edit —
  // the backend still sniffs pasted CSV as a safety net.
  const [kind, setKind] = useState<'doc' | 'csv'>('doc');
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
  }, [refresh, refreshToken]);

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    const text = await readFileText(file);
    const isCsv = /\.csv$/i.test(file.name) || file.type === 'text/csv';
    setKind(isCsv ? 'csv' : 'doc');
    setContent(text);
    if (!name.trim()) setName(file.name.replace(/\.(md|markdown|txt|csv)$/i, ''));
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
        body: JSON.stringify({ name: name.trim() || 'Document', content, kind }),
      });
      const data = (await res.json()) as { ok?: boolean; error?: string };
      if (!res.ok || !data.ok) {
        setError(data.error || `upload failed (HTTP ${res.status})`);
        return;
      }
      setName('');
      setContent('');
      setKind('doc');
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

  // Real, summarized context for the collapsed header chips — no fabrication: counts
  // come straight from the live document list (CSV rows = the per-row chunk count).
  const csvRows = docs.reduce((n, d) => (d.kind === 'csv' && d.chunks ? n + d.chunks : n), 0);
  const csvCount = docs.filter((d) => d.kind === 'csv').length;
  const docCount = docs.length - csvCount;
  const summaryChips: string[] = [];
  if (csvCount > 0) summaryChips.push(csvRows > 0 ? `CSV · ${csvRows} rows` : `${csvCount} CSV`);
  if (docCount > 0) summaryChips.push(`${docCount} doc${docCount === 1 ? '' : 's'}`);

  const summaryText = !endpoint
    ? 'Needs the live studio backend'
    : !loaded
      ? 'Loading…'
      : docs.length === 0
        ? 'No documents yet'
        : null; // chips carry the summary when we have docs

  const showBody = !collapsible || open;

  return (
    <div
      aria-label="Knowledge documents"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: showBody ? 10 : 0,
        border: '1px solid var(--hairline)',
        borderRadius: 12,
        background: '#fff',
        padding: collapsible ? 0 : 12,
        minWidth: 0,
        overflow: 'hidden',
      }}
    >
      {collapsible ? (
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-label="Toggle knowledge documents"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            width: '100%',
            minWidth: 0,
            padding: '10px 12px',
            border: 'none',
            background: 'transparent',
            cursor: 'pointer',
            textAlign: 'left',
          }}
        >
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="#8C877D"
            strokeWidth="2.4"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
            style={{ flex: '0 0 auto', transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 140ms ease' }}
          >
            <polyline points="9 6 15 12 9 18" />
          </svg>
          <span style={{ fontSize: 12.5, fontWeight: 600, color: '#46423B', flex: '0 0 auto' }}>Context</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', minWidth: 0, flex: 1 }}>
            {summaryText ? (
              <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>{summaryText}</span>
            ) : (
              summaryChips.map((c) => (
                <span
                  key={c}
                  style={{
                    fontSize: 10.5,
                    color: '#46423B',
                    background: 'var(--surface-alt)',
                    border: '1px solid var(--hairline)',
                    borderRadius: 999,
                    padding: '2px 9px',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {c}
                </span>
              ))
            )}
          </div>
          <span style={{ fontSize: 11, color: '#0F8A82', fontWeight: 600, flex: '0 0 auto' }}>
            {open ? 'Hide' : 'Manage'}
          </span>
        </button>
      ) : (
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <span style={{ fontSize: 12.5, fontWeight: 600, color: '#46423B' }}>Knowledge</span>
          <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
            Documents the whole team reads
          </span>
        </div>
      )}

      {showBody && (
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
          minWidth: 0,
          maxHeight: collapsible ? 340 : undefined,
          overflowY: collapsible ? 'auto' : undefined,
          padding: collapsible ? '0 12px 12px' : 0,
        }}
      >
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
                      display: '-webkit-box',
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical',
                      color: '#8C877D',
                      overflow: 'hidden',
                      wordBreak: 'break-word',
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
          onChange={(e) => {
            setContent(e.target.value);
            setKind('doc');
          }}
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
            accept=".txt,.md,.markdown,.csv,text/plain,text/csv"
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
        <div style={{ fontSize: 10.5, color: '#8C877D', lineHeight: 1.4 }}>
          A CSV added here is persistent reference knowledge every agent can read and
          cite; to blast a campaign to exactly those leads, use the lead upload on the
          Voice / Agency campaign flow instead.
        </div>
      </div>

      {error ? (
        <div role="alert" style={{ fontSize: 11, color: '#B42318' }}>
          {error}
        </div>
      ) : null}
      </div>
      )}
    </div>
  );
}
