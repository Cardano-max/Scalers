'use client';

/**
 * PlanDocPanel — the editable campaign plan/spec document (ADR Decision 5b: the
 * living spec, not a chat transcript).
 *
 * An editable text area bound to the plan body, a version label, and Approve /
 * Execute action buttons. The body edits are REAL local state. The version label
 * reflects the loaded PlanDoc.
 *
 * HONESTY: Approve and Execute are DISABLED placeholders while the studio is not
 * wired — savePlanDoc/approvePlan are not connected to a live backend, so the
 * panel must not imply the plan can be approved or run. A preview banner states
 * this explicitly.
 */
import type { PlanDoc, PlanDocStatus } from '@/lib/data/studio-adapter';

interface PlanDocPanelProps {
  doc: PlanDoc | null;
  loading?: boolean;
  /** Current editable body (parent-owned local state). */
  body: string;
  onChangeBody: (next: string) => void;
  /** When true (preview), Approve/Execute render as disabled placeholders. */
  notWired: boolean;
  onApprove?: () => void;
  onExecute?: () => void;
}

const STATUS_LABEL: Record<PlanDocStatus, string> = {
  draft: 'Draft',
  approved: 'Approved',
  executing: 'Executing',
  executed: 'Executed',
};

export function PlanDocPanel({
  doc,
  loading = false,
  body,
  onChangeBody,
  notWired,
  onApprove,
  onExecute,
}: PlanDocPanelProps) {
  const version = doc ? `v${doc.version}` : 'v—';
  const status = doc ? STATUS_LABEL[doc.status] : '—';
  const disabled = notWired;

  return (
    <section
      aria-label="Campaign plan document"
      style={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        background: '#fff',
        border: '1px solid var(--hairline)',
        borderRadius: 12,
        overflow: 'hidden',
      }}
    >
      <header
        style={{
          padding: '12px 16px',
          borderBottom: '1px solid var(--hairline)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
        }}
      >
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: '#1A1A17' }}>
          Plan / spec
        </h2>
        <span
          aria-label="Plan version"
          style={{
            fontSize: 11,
            fontWeight: 600,
            color: '#6B6461',
            fontFamily: "'IBM Plex Mono', monospace",
            padding: '2px 8px',
            background: '#F5F3F0',
            borderRadius: 6,
          }}
        >
          {version} · {status}
        </span>
      </header>

      {notWired && (
        <div
          role="note"
          style={{
            margin: '12px 16px 0',
            padding: '8px 12px',
            background: '#FEF9F5',
            border: '1px solid #F0E6D8',
            borderRadius: 9,
            fontSize: 12,
            lineHeight: 1.4,
            color: '#8B6F47',
          }}
        >
          Preview — not connected to live agents yet. You can edit this scaffold
          locally; Approve and Execute are disabled until the studio backend is
          wired. Nothing is saved or run.
        </div>
      )}

      <div style={{ flex: 1, minHeight: 0, padding: '12px 16px', display: 'flex' }}>
        {loading ? (
          <div role="status" style={{ margin: 'auto', color: '#8C877D', fontSize: 13 }}>
            Loading plan…
          </div>
        ) : (
          <textarea
            aria-label="Editable plan document"
            value={body}
            onChange={(e) => onChangeBody(e.target.value)}
            spellCheck={false}
            style={{
              flex: 1,
              resize: 'none',
              fontSize: 13,
              lineHeight: 1.55,
              fontFamily: "'IBM Plex Mono', monospace",
              padding: '12px',
              border: '1px solid var(--hairline)',
              borderRadius: 9,
              outline: 'none',
              background: '#FBFAF7',
              color: '#2A2722',
            }}
          />
        )}
      </div>

      <footer
        style={{
          padding: '12px 16px',
          borderTop: '1px solid var(--hairline)',
          display: 'flex',
          gap: 8,
        }}
      >
        <button
          type="button"
          onClick={onApprove}
          disabled={disabled}
          title={disabled ? 'Not wired yet — studio backend pending' : 'Approve plan'}
          style={{
            flex: 1,
            fontSize: 13,
            fontWeight: 600,
            padding: '10px 14px',
            border: '1px solid var(--hairline)',
            borderRadius: 9,
            background: disabled ? '#F1EFEA' : '#fff',
            color: disabled ? '#A8A299' : '#46423B',
            cursor: disabled ? 'not-allowed' : 'pointer',
          }}
        >
          Approve plan
        </button>
        <button
          type="button"
          onClick={onExecute}
          disabled={disabled}
          title={disabled ? 'Not wired yet — studio backend pending' : 'Execute campaign'}
          style={{
            flex: 1,
            fontSize: 13,
            fontWeight: 600,
            padding: '10px 14px',
            border: 'none',
            borderRadius: 9,
            background: disabled ? '#CFE7E4' : '#0F8A82',
            color: disabled ? '#7FA8A3' : '#fff',
            cursor: disabled ? 'not-allowed' : 'pointer',
          }}
        >
          Execute
        </button>
      </footer>
    </section>
  );
}
