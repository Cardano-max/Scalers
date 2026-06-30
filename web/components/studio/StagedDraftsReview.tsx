'use client';

/**
 * StagedDraftsReview — the result / review surface that the run transitions into
 * when it completes. It renders ONE card per REAL HELD draft (a PENDING `actions`
 * row exposed by GET /studio/run/{id}.pending) with clean, actionable controls:
 *
 *   ✓ Approve   — the EXISTING approve→publish path (adapter.approveAction). That
 *                 mutation is the only send path; it already routes the gmail safety
 *                 + allow-list server-side. We do NOT bypass it.
 *   ✕ Reject    — discard/block (adapter.rejectAction).
 *   ⤢ Deep Review — navigate into the Review Queue detail for full trace inspection.
 *
 * HONESTY: bound to real action rows only; an empty list shows nothing. Approve can
 * come back FAILED with the verbatim provider error (e.g. an expired token) — we do
 * NOT claim "sent"; the card flips to a failed state showing WHY. Nothing here sends
 * on its own — the operator's Approve is the single trigger.
 */
import { useEffect, useMemo, useState } from 'react';
import { useData } from '@/lib/data/DataProvider';
import type { PendingAction } from '@/lib/studio/run-trace';
import { personaForRunRole } from '@/lib/studio/agency';

const TEAL = '#0F8A82';

type CardState =
  | { kind: 'idle' }
  | { kind: 'busy' }
  | { kind: 'sent' }
  | { kind: 'rejected' }
  | { kind: 'failed'; error: string };

export function StagedDraftsReview({
  pending,
  onDeepReview,
}: {
  pending: PendingAction[];
  /** Navigate into the Review Queue detail focused on this action. */
  onDeepReview?: (actionId: string) => void;
}) {
  const { adapter } = useData();
  // Per-action lifecycle state, keyed by id. Approve/reject flip a card in place so
  // the operator sees the honest outcome (sent / rejected / failed) without a refetch.
  const [states, setStates] = useState<Record<string, CardState>>({});

  // Drop lifecycle entries for actions no longer present (a fresh run replaced them).
  useEffect(() => {
    const ids = new Set(pending.map((p) => p.id));
    setStates((prev) => {
      const next: Record<string, CardState> = {};
      for (const [k, v] of Object.entries(prev)) if (ids.has(k)) next[k] = v;
      return next;
    });
  }, [pending]);

  const liveCount = useMemo(
    () => pending.filter((p) => (states[p.id]?.kind ?? 'idle') !== 'sent' && (states[p.id]?.kind ?? 'idle') !== 'rejected').length,
    [pending, states],
  );

  if (pending.length === 0) return null;

  const setState = (id: string, s: CardState) => setStates((prev) => ({ ...prev, [id]: s }));

  const approve = async (a: PendingAction) => {
    setState(a.id, { kind: 'busy' });
    try {
      const result = await adapter.approveAction(a.id, a.idempotencyKey);
      if (result.status === 'FAILED') {
        setState(a.id, { kind: 'failed', error: result.lastError ?? 'provider error' });
        return;
      }
      setState(a.id, { kind: 'sent' });
    } catch (e) {
      setState(a.id, { kind: 'failed', error: e instanceof Error ? e.message : String(e) });
    }
  };

  const reject = async (a: PendingAction) => {
    setState(a.id, { kind: 'busy' });
    try {
      await adapter.rejectAction(a.id);
      setState(a.id, { kind: 'rejected' });
    } catch (e) {
      setState(a.id, { kind: 'failed', error: e instanceof Error ? e.message : String(e) });
    }
  };

  return (
    <section
      aria-label="Staged drafts — review"
      className="spring-in"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        background: '#fff',
        border: '1px solid var(--hairline-strong)',
        borderRadius: 'var(--radius-card)',
        padding: 14,
        boxShadow: 'var(--shadow-card)',
      }}
    >
      <header style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>
          Review the drafts
        </h3>
        <span style={{ flex: 1 }} />
        <span
          style={{
            fontSize: 11,
            fontWeight: 600,
            color: 'var(--amber-text)',
            background: 'var(--amber-bg)',
            border: '1px solid var(--amber-border)',
            borderRadius: 'var(--radius-pill)',
            padding: '3px 9px',
            fontVariantNumeric: 'tabular-nums',
            whiteSpace: 'nowrap',
          }}
        >
          {liveCount} HELD
        </span>
      </header>
      <p style={{ margin: 0, fontSize: 11.5, color: 'var(--text-muted)', lineHeight: 1.45 }}>
        Nothing is sent until you approve. Approve runs the real publish path (safety +
        allow-list checks server-side); Reject discards; Deep Review opens the full trace.
      </p>

      {pending.map((a) => (
        <DraftCard
          key={a.id}
          action={a}
          state={states[a.id] ?? { kind: 'idle' }}
          onApprove={() => approve(a)}
          onReject={() => reject(a)}
          onDeepReview={onDeepReview ? () => onDeepReview(a.id) : undefined}
        />
      ))}
    </section>
  );
}

function DraftCard({
  action,
  state,
  onApprove,
  onReject,
  onDeepReview,
}: {
  action: PendingAction;
  state: CardState;
  onApprove: () => void;
  onReject: () => void;
  onDeepReview?: () => void;
}) {
  const persona = personaForRunRole(action.channel || '');
  const busy = state.kind === 'busy';
  const resolved = state.kind === 'sent' || state.kind === 'rejected';
  const channel = (action.channel || 'draft').toUpperCase();

  return (
    <article
      data-action-id={action.id}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        border: '1px solid var(--hairline)',
        borderLeft: `3px solid ${persona.accent}`,
        borderRadius: 10,
        padding: '10px 12px',
        background: resolved ? 'var(--surface-alt)' : '#fff',
        opacity: resolved ? 0.72 : 1,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span
          style={{
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: '0.02em',
            color: persona.accent,
            background: persona.bg,
            border: `1px solid ${persona.border}`,
            borderRadius: 'var(--radius-pill)',
            padding: '2px 8px',
          }}
        >
          {channel}
        </span>
        {action.target && (
          <span style={{ fontSize: 12, color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 220 }}>
            {action.target}
          </span>
        )}
      </div>

      {action.subject && (
        <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink)' }}>{action.subject}</div>
      )}
      <div
        style={{
          fontSize: 12.5,
          lineHeight: 1.5,
          color: 'var(--text-secondary)',
          display: '-webkit-box',
          WebkitLineClamp: 4,
          WebkitBoxOrient: 'vertical',
          overflow: 'hidden',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {action.draft || '(empty draft)'}
      </div>

      {state.kind === 'failed' && (
        <div
          role="alert"
          style={{
            fontSize: 11.5,
            color: 'var(--danger-text)',
            background: 'var(--danger-bg)',
            border: '1px solid #F1BEB8',
            borderRadius: 8,
            padding: '6px 9px',
          }}
        >
          Send failed — {state.error}
        </div>
      )}

      {state.kind === 'sent' ? (
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--success-text)' }}>✓ Approved — published</div>
      ) : state.kind === 'rejected' ? (
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)' }}>✕ Rejected</div>
      ) : (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button
            type="button"
            onClick={onApprove}
            disabled={busy}
            aria-label="Approve and publish"
            style={{
              fontSize: 12.5,
              fontWeight: 600,
              color: '#fff',
              background: TEAL,
              border: 'none',
              borderRadius: 'var(--radius-button)',
              padding: '7px 14px',
              cursor: busy ? 'wait' : 'pointer',
              opacity: busy ? 0.6 : 1,
            }}
          >
            {busy && state.kind === 'busy' ? 'Working…' : '✓ Approve'}
          </button>
          <button
            type="button"
            onClick={onReject}
            disabled={busy}
            aria-label="Reject draft"
            style={{
              fontSize: 12.5,
              fontWeight: 600,
              color: 'var(--danger-text)',
              background: '#fff',
              border: '1px solid #E7C3BD',
              borderRadius: 'var(--radius-button)',
              padding: '7px 14px',
              cursor: busy ? 'not-allowed' : 'pointer',
              opacity: busy ? 0.6 : 1,
            }}
          >
            ✕ Reject
          </button>
          {onDeepReview && (
            <button
              type="button"
              onClick={onDeepReview}
              aria-label="Open deep review"
              style={{
                fontSize: 12.5,
                fontWeight: 600,
                color: 'var(--teal-dark)',
                background: '#fff',
                border: '1px solid var(--hairline-strong)',
                borderRadius: 'var(--radius-button)',
                padding: '7px 14px',
                cursor: 'pointer',
                marginLeft: 'auto',
              }}
            >
              Deep Review ⤢
            </button>
          )}
        </div>
      )}
    </article>
  );
}
