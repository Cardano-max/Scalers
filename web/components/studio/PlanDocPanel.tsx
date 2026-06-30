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
import type { CampaignPlan } from '@/lib/studio/agui';

interface PlanDocPanelProps {
  doc: PlanDoc | null;
  loading?: boolean;
  /** Current editable body (parent-owned local state). Used in the preview path. */
  body: string;
  onChangeBody: (next: string) => void;
  /** When true (preview), Approve/Execute render as disabled placeholders. */
  notWired: boolean;
  onApprove?: () => void;
  onExecute?: () => void;
  /** LIVE path: when set, edit the structured AG-UI shared-state plan directly. A
   *  field edit calls `onChangeField`; "Apply edits & re-plan" syncs to the backend
   *  (the host re-plans from the edited state). Mutually exclusive with the body path. */
  plan?: CampaignPlan | null;
  onChangeField?: (patch: Partial<CampaignPlan>) => void;
  onApplyReplan?: () => void;
  planDirty?: boolean;
  busy?: boolean;
}

const fieldLabelStyle: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: 0.4,
  color: '#8C877D',
  marginBottom: 4,
  display: 'block',
};

const fieldInputStyle: React.CSSProperties = {
  width: '100%',
  fontSize: 13,
  fontFamily: 'inherit',
  padding: '8px 10px',
  border: '1px solid var(--hairline)',
  borderRadius: 8,
  outline: 'none',
  background: '#FBFAF7',
  color: '#2A2722',
  boxSizing: 'border-box',
};

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
  plan = null,
  onChangeField,
  onApplyReplan,
  planDirty = false,
  busy = false,
}: PlanDocPanelProps) {
  const version = doc ? `v${doc.version}` : 'v—';
  const status = doc ? STATUS_LABEL[doc.status] : '—';
  const disabled = notWired;
  const live = plan != null;

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

      {live && (
        <div
          role="note"
          style={{
            margin: '12px 16px 0',
            padding: '8px 12px',
            background: '#EEF6FF',
            border: '1px solid #CFE2FB',
            borderRadius: 9,
            fontSize: 12,
            lineHeight: 1.4,
            color: '#1E4E8C',
          }}
        >
          Live shared state. Edit a field, then <strong>Apply edits &amp; re-plan</strong>{' '}
          to sync it to the backend — the host re-plans from your change and the plan
          syncs back here.
        </div>
      )}

      <div style={{ flex: 1, minHeight: 0, padding: '12px 16px', display: 'flex' }}>
        {loading ? (
          <div role="status" style={{ margin: 'auto', color: '#8C877D', fontSize: 13 }}>
            Loading plan…
          </div>
        ) : live && plan ? (
          <div
            style={{
              flex: 1,
              overflowY: 'auto',
              display: 'flex',
              flexDirection: 'column',
              gap: 12,
            }}
          >
            <div>
              <label style={fieldLabelStyle} htmlFor="plan-goal">Goal</label>
              <input
                id="plan-goal"
                aria-label="Plan goal"
                value={plan.goal}
                onChange={(e) => onChangeField?.({ goal: e.target.value })}
                placeholder="e.g. fill empty Tuesday slots in May"
                style={fieldInputStyle}
              />
            </div>
            <div>
              <label style={fieldLabelStyle} htmlFor="plan-audience">Audience</label>
              <input
                id="plan-audience"
                aria-label="Plan audience"
                value={plan.audience}
                onChange={(e) => onChangeField?.({ audience: e.target.value })}
                placeholder="e.g. local fine-line tattoo fans"
                style={fieldInputStyle}
              />
            </div>
            <div>
              <label style={fieldLabelStyle} htmlFor="plan-channels">Channels (comma-separated)</label>
              <input
                id="plan-channels"
                aria-label="Plan channels"
                value={plan.channels.join(', ')}
                onChange={(e) =>
                  onChangeField?.({
                    channels: e.target.value
                      .split(',')
                      .map((s) => s.trim())
                      .filter(Boolean),
                  })
                }
                placeholder="instagram, email"
                style={fieldInputStyle}
              />
            </div>
            {plan.sections.length > 0 && (
              <div>
                <span style={fieldLabelStyle}>Sections</span>
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, color: '#2A2722' }}>
                  {plan.sections.map((s, i) => (
                    <li key={i}>{s}</li>
                  ))}
                </ul>
              </div>
            )}
            {plan.assets.length > 0 && (
              <div>
                <span style={fieldLabelStyle}>Planned assets ({plan.assets.length})</span>
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: '#46423B' }}>
                  {plan.assets.slice(0, 8).map((a, i) => (
                    <li key={i}>
                      {String((a.asset_type as string) ?? (a.stage as string) ?? 'asset')}
                    </li>
                  ))}
                </ul>
              </div>
            )}
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

      {live ? (
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
            onClick={onApplyReplan}
            disabled={!planDirty || busy}
            title={
              planDirty
                ? 'Sync your edits to the backend and re-plan'
                : 'Edit a field to enable'
            }
            style={{
              flex: 1,
              fontSize: 13,
              fontWeight: 600,
              padding: '10px 14px',
              border: 'none',
              borderRadius: 9,
              background: !planDirty || busy ? '#CFE7E4' : '#0F8A82',
              color: !planDirty || busy ? '#7FA8A3' : '#fff',
              cursor: !planDirty || busy ? 'not-allowed' : 'pointer',
            }}
          >
            {planDirty ? 'Apply edits & re-plan' : 'Plan in sync'}
          </button>
        </footer>
      ) : (
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
      )}
    </section>
  );
}
