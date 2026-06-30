'use client';

/**
 * CampaignSendControls — the campaign-level SAFE-SEND surface for a finished run.
 *
 * It classifies the run's staged drafts into the ones that clear the safety bar
 * (eligible) and the ones that do NOT (review_required), then offers exactly two
 * paths:
 *
 *   Send eligible (N) — a ONE-CLICK send of ONLY the eligible drafts, behind an
 *                       inline Confirm. There is deliberately NO "send all" / "send
 *                       everything" affordance: held drafts are never swept along.
 *   Override and send — a per-draft, REASON-REQUIRED, AUDITED bypass of the bar for
 *                       a single held draft. The Send button stays disabled until a
 *                       reason is typed; the copy makes the bypass + audit explicit.
 *
 * HONESTY: classify is read-only. Send/override results are shown verbatim (n_sent /
 * n_failed / n_skipped, or the engine's error) — never a fabricated success. Both
 * lists empty renders the honest "nothing staged yet" state.
 */
import { useCallback, useEffect, useState } from 'react';
import {
  classifyCampaign,
  sendEligible,
  overrideSend,
  type CampaignClassification,
  type CampaignDraft,
  type OverrideResult,
  type SendEligibleResult,
  type SendMode,
} from '@/lib/studio/campaign-send';

const TEAL = '#0F8A82';

type OverrideState =
  | { kind: 'idle' }
  | { kind: 'busy' }
  | { kind: 'done'; result: OverrideResult }
  | { kind: 'error'; error: string };

export function CampaignSendControls({ runId }: { runId: string }) {
  const [data, setData] = useState<CampaignClassification | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Send-eligible flow: inline Confirm -> send -> show result + re-classify.
  const [confirming, setConfirming] = useState(false);
  const [sending, setSending] = useState(false);
  const [sendResult, setSendResult] = useState<SendEligibleResult | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);

  // Send mode: default Test (safe) — sends are rerouted to the operator inbox with a
  // [TEST] marker. Live is the EXPLICIT operator authorization that lets a send reach
  // the real recipient with a clean subject. It applies to BOTH send-eligible and
  // override; it is independent of eligibility (which is a confidence/compliance gate).
  const [liveMode, setLiveMode] = useState(false);

  // Per-draft override flow: which held draft's form is open, its typed reason, and
  // the lifecycle of each override keyed by action_id.
  const [overrideOpen, setOverrideOpen] = useState<string | null>(null);
  const [overrideReason, setOverrideReason] = useState('');
  const [overrideStates, setOverrideStates] = useState<Record<string, OverrideState>>({});

  const classify = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await classifyCampaign(runId));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    void classify();
  }, [classify]);

  const doSendEligible = async () => {
    setSending(true);
    setSendError(null);
    try {
      const r = await sendEligible(runId, undefined, liveMode);
      setSendResult(r);
      setConfirming(false);
      await classify();
    } catch (e) {
      setSendError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  };

  const setOverrideState = (id: string, s: OverrideState) =>
    setOverrideStates((prev) => ({ ...prev, [id]: s }));

  const submitOverride = async (actionId: string) => {
    const reason = overrideReason.trim();
    if (!reason) return;
    setOverrideState(actionId, { kind: 'busy' });
    try {
      const r = await overrideSend(actionId, reason, undefined, liveMode);
      setOverrideState(actionId, { kind: 'done', result: r });
      setOverrideOpen(null);
      setOverrideReason('');
    } catch (e) {
      setOverrideState(actionId, { kind: 'error', error: e instanceof Error ? e.message : String(e) });
    }
  };

  const nEligible = data?.n_eligible ?? 0;
  const reviewRequired = data?.review_required ?? [];
  const bothEmpty = !!data && nEligible === 0 && reviewRequired.length === 0;

  return (
    <section
      aria-label="Campaign send controls"
      className="spring-in"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
        background: '#fff',
        border: '1px solid var(--hairline-strong)',
        borderRadius: 'var(--radius-card)',
        padding: 14,
        boxShadow: 'var(--shadow-card)',
      }}
    >
      <header style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>
          Safe send
        </h3>
        <span style={{ flex: 1 }} />
        <button
          type="button"
          onClick={() => void classify()}
          disabled={loading}
          aria-label="Refresh classification"
          style={{
            fontSize: 12,
            fontWeight: 600,
            color: 'var(--teal-dark)',
            background: '#fff',
            border: '1px solid var(--hairline-strong)',
            borderRadius: 'var(--radius-button)',
            padding: '5px 12px',
            cursor: loading ? 'wait' : 'pointer',
            opacity: loading ? 0.6 : 1,
          }}
        >
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </header>
      <p style={{ margin: 0, fontSize: 11.5, color: 'var(--text-muted)', lineHeight: 1.45 }}>
        Only drafts above the confidence bar can be sent in one click. Everything else is
        held and needs an explicit, audited override. Nothing below the bar is ever swept along.
      </p>

      {/* ── Send mode: default Test (safe redirect) vs explicit Live ───────────── */}
      <div
        role="group"
        aria-label="Send mode"
        style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}
      >
        <span style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--text-secondary)' }}>Mode</span>
        <div
          style={{
            display: 'inline-flex',
            border: '1px solid var(--hairline-strong)',
            borderRadius: 'var(--radius-pill)',
            overflow: 'hidden',
          }}
        >
          <button
            type="button"
            aria-pressed={!liveMode}
            onClick={() => setLiveMode(false)}
            style={{
              fontSize: 11.5,
              fontWeight: 600,
              padding: '4px 12px',
              border: 'none',
              cursor: 'pointer',
              color: !liveMode ? '#fff' : 'var(--text-secondary)',
              background: !liveMode ? TEAL : '#fff',
            }}
          >
            Test (safe)
          </button>
          <button
            type="button"
            aria-pressed={liveMode}
            onClick={() => setLiveMode(true)}
            style={{
              fontSize: 11.5,
              fontWeight: 600,
              padding: '4px 12px',
              border: 'none',
              borderLeft: '1px solid var(--hairline-strong)',
              cursor: 'pointer',
              color: liveMode ? '#fff' : 'var(--text-secondary)',
              background: liveMode ? 'var(--danger-text)' : '#fff',
            }}
          >
            Live
          </button>
        </div>
        <ModeBadge mode={liveMode ? 'live' : 'test_redirect'} />
      </div>
      <p style={{ margin: 0, fontSize: 11, color: liveMode ? 'var(--danger-text)' : 'var(--text-muted)', lineHeight: 1.45 }}>
        {liveMode
          ? 'Live: sends reach the REAL recipient with a clean subject. Use only when you intend real outreach.'
          : 'Test: every send is rerouted to the operator inbox with a [TEST] marker — no real recipient is contacted.'}
      </p>

      {error && (
        <div
          role="alert"
          style={{
            fontSize: 12,
            color: 'var(--danger-text)',
            background: 'var(--danger-bg)',
            border: '1px solid #F1BEB8',
            borderRadius: 8,
            padding: '8px 11px',
          }}
        >
          Could not classify drafts — {error}
        </div>
      )}

      {loading && !data && (
        <div className="shimmer" style={{ height: 44, borderRadius: 9, border: '1px solid var(--hairline)' }} aria-label="classifying drafts" />
      )}

      {bothEmpty && (
        <p style={{ margin: 0, fontSize: 12.5, color: 'var(--text-muted)' }}>
          No drafts staged for this run yet.
        </p>
      )}

      {/* ── Eligible: the one-click send of ONLY the safe drafts ─────────────── */}
      {data && nEligible > 0 && (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 9,
            border: '1px solid var(--hairline)',
            borderLeft: `3px solid ${TEAL}`,
            borderRadius: 10,
            padding: '11px 13px',
            background: 'rgba(15,138,130,0.04)',
          }}
        >
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>
            {nEligible} draft{nEligible === 1 ? '' : 's'} safe to send
          </div>
          {!confirming ? (
            <button
              type="button"
              onClick={() => setConfirming(true)}
              disabled={sending}
              style={{
                alignSelf: 'flex-start',
                fontSize: 13,
                fontWeight: 600,
                color: '#fff',
                background: TEAL,
                border: 'none',
                borderRadius: 'var(--radius-button)',
                padding: '9px 16px',
                cursor: sending ? 'wait' : 'pointer',
                opacity: sending ? 0.6 : 1,
                boxShadow: 'var(--shadow-selected)',
              }}
            >
              Send eligible ({nEligible})
            </button>
          ) : (
            <div
              role="group"
              aria-label="Confirm send eligible"
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 9,
                background: '#fff',
                border: '1px solid var(--hairline-strong)',
                borderRadius: 9,
                padding: 11,
              }}
            >
              <p style={{ margin: 0, fontSize: 12.5, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                Send the {nEligible} eligible draft{nEligible === 1 ? '' : 's'}? Each goes through
                approval + send; nothing below the bar is touched.
              </p>
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  type="button"
                  onClick={() => void doSendEligible()}
                  disabled={sending}
                  style={{
                    fontSize: 12.5,
                    fontWeight: 600,
                    color: '#fff',
                    background: TEAL,
                    border: 'none',
                    borderRadius: 'var(--radius-button)',
                    padding: '8px 16px',
                    cursor: sending ? 'wait' : 'pointer',
                    opacity: sending ? 0.6 : 1,
                  }}
                >
                  {sending ? 'Sending…' : 'Confirm send'}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirming(false)}
                  disabled={sending}
                  style={{
                    fontSize: 12.5,
                    fontWeight: 600,
                    color: 'var(--text-secondary)',
                    background: '#fff',
                    border: '1px solid var(--hairline-strong)',
                    borderRadius: 'var(--radius-button)',
                    padding: '8px 16px',
                    cursor: sending ? 'not-allowed' : 'pointer',
                    opacity: sending ? 0.6 : 1,
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {sendError && (
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
              Send failed — {sendError}
            </div>
          )}

          {sendResult && (
            <div
              style={{
                fontSize: 12,
                color: 'var(--text-secondary)',
                display: 'flex',
                gap: 10,
                flexWrap: 'wrap',
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              <span style={{ color: 'var(--success-text)', fontWeight: 600 }}>{sendResult.n_sent} sent</span>
              <span style={{ color: sendResult.n_failed > 0 ? 'var(--danger-text)' : 'var(--text-muted)' }}>
                {sendResult.n_failed} failed
              </span>
              <span style={{ color: 'var(--text-muted)' }}>{sendResult.n_skipped} skipped</span>
              {/* Per-send mode from the engine response — the Live Feed reads these so
                  the operator can see exactly how each send was routed. */}
              {(() => {
                const liveSent = sendResult.sent.filter((s) => s.mode === 'live').length;
                const testSent = sendResult.sent.filter((s) => s.mode === 'test_redirect').length;
                if (liveSent === 0 && testSent === 0) return null;
                return (
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    {liveSent > 0 && (
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                        <ModeBadge mode="live" />
                        {liveSent}
                      </span>
                    )}
                    {testSent > 0 && (
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                        <ModeBadge mode="test_redirect" />
                        {testSent}
                      </span>
                    )}
                  </span>
                );
              })()}
            </div>
          )}
        </div>
      )}

      {/* ── Review-required: held drafts, each behind an audited override ──────── */}
      {data && reviewRequired.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--amber-text)' }}>
            {reviewRequired.length} draft{reviewRequired.length === 1 ? '' : 's'} need review
          </div>
          {reviewRequired.map((d) => (
            <OverrideRow
              key={d.action_id}
              draft={d}
              open={overrideOpen === d.action_id}
              reason={overrideOpen === d.action_id ? overrideReason : ''}
              state={overrideStates[d.action_id] ?? { kind: 'idle' }}
              onOpen={() => {
                setOverrideOpen(d.action_id);
                setOverrideReason('');
              }}
              onCancel={() => {
                setOverrideOpen(null);
                setOverrideReason('');
              }}
              onReason={setOverrideReason}
              onSubmit={() => void submitOverride(d.action_id)}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function OverrideRow({
  draft,
  open,
  reason,
  state,
  onOpen,
  onCancel,
  onReason,
  onSubmit,
}: {
  draft: CampaignDraft;
  open: boolean;
  reason: string;
  state: OverrideState;
  onOpen: () => void;
  onCancel: () => void;
  onReason: (v: string) => void;
  onSubmit: () => void;
}) {
  const channel = (draft.channel || 'draft').toUpperCase();
  const busy = state.kind === 'busy';
  const done = state.kind === 'done';

  return (
    <article
      data-action-id={draft.action_id}
      data-eligible="false"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        border: '1px solid var(--hairline)',
        borderLeft: '3px solid var(--amber-border)',
        borderRadius: 10,
        padding: '10px 12px',
        background: done ? 'var(--surface-alt)' : '#fff',
        opacity: done ? 0.78 : 1,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span
          style={{
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: '0.02em',
            color: 'var(--amber-text)',
            background: 'var(--amber-bg)',
            border: '1px solid var(--amber-border)',
            borderRadius: 'var(--radius-pill)',
            padding: '2px 8px',
          }}
        >
          {channel}
        </span>
        {draft.target && (
          <span style={{ fontSize: 12, color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 220 }}>
            {draft.target}
          </span>
        )}
      </div>

      <div style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>
        Held — {draft.reason}
      </div>

      {state.kind === 'error' && (
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
          Override failed — {state.error}
        </div>
      )}

      {done ? (
        <div style={{ fontSize: 12, fontWeight: 600, color: state.result.ok ? 'var(--success-text)' : 'var(--danger-text)' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            {state.result.ok ? '✓ Overridden and sent' : `Override recorded — ${state.result.last_error ?? 'send did not complete'}`}
            {state.result.mode && <ModeBadge mode={state.result.mode} />}
          </span>
          <div style={{ fontSize: 11, fontWeight: 500, color: 'var(--text-muted)', marginTop: 2 }}>
            An audit entry was recorded for this override.
          </div>
        </div>
      ) : open ? (
        <div
          role="group"
          aria-label="Override and send"
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            background: 'var(--amber-bg)',
            border: '1px solid var(--amber-border)',
            borderRadius: 9,
            padding: 11,
          }}
        >
          <p style={{ margin: 0, fontSize: 11.5, color: 'var(--amber-text)', lineHeight: 1.5 }}>
            This bypasses the safety bar and sends this held draft. The action is audited:
            your reason is recorded. A typed reason is required.
          </p>
          <textarea
            value={reason}
            onChange={(e) => onReason(e.target.value)}
            placeholder="Why is this safe to send anyway?"
            aria-label="Override reason"
            rows={2}
            style={{
              fontSize: 12.5,
              padding: '8px 10px',
              borderRadius: 'var(--radius-button)',
              border: '1px solid var(--hairline-strong)',
              background: '#fff',
              color: 'var(--ink)',
              resize: 'vertical',
              fontFamily: 'inherit',
            }}
          />
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              type="button"
              onClick={onSubmit}
              disabled={busy || !reason.trim()}
              aria-label="Override and send draft"
              style={{
                fontSize: 12.5,
                fontWeight: 600,
                color: '#fff',
                background: busy || !reason.trim() ? '#C8A98F' : 'var(--amber-text)',
                border: 'none',
                borderRadius: 'var(--radius-button)',
                padding: '8px 16px',
                cursor: busy || !reason.trim() ? 'not-allowed' : 'pointer',
              }}
            >
              {busy ? 'Sending…' : 'Override and send'}
            </button>
            <button
              type="button"
              onClick={onCancel}
              disabled={busy}
              style={{
                fontSize: 12.5,
                fontWeight: 600,
                color: 'var(--text-secondary)',
                background: '#fff',
                border: '1px solid var(--hairline-strong)',
                borderRadius: 'var(--radius-button)',
                padding: '8px 16px',
                cursor: busy ? 'not-allowed' : 'pointer',
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={onOpen}
          aria-label="Open override and send"
          style={{
            alignSelf: 'flex-start',
            fontSize: 12.5,
            fontWeight: 600,
            color: 'var(--amber-text)',
            background: '#fff',
            border: '1px solid var(--amber-border)',
            borderRadius: 'var(--radius-button)',
            padding: '7px 14px',
            cursor: 'pointer',
          }}
        >
          Override &amp; send
        </button>
      )}
    </article>
  );
}

/** A small pill badging the resolved send mode — Live (real recipient, clean) vs Test
 *  (rerouted to the operator inbox with a [TEST] marker). Honest: it reflects the mode
 *  the engine actually reported, never an assumed one. */
function ModeBadge({ mode }: { mode: SendMode }) {
  const live = mode === 'live';
  return (
    <span
      data-mode={mode}
      style={{
        fontSize: 10,
        fontWeight: 700,
        letterSpacing: '0.02em',
        color: live ? '#fff' : 'var(--text-secondary)',
        background: live ? 'var(--danger-text)' : 'var(--surface-alt)',
        border: `1px solid ${live ? 'var(--danger-text)' : 'var(--hairline-strong)'}`,
        borderRadius: 'var(--radius-pill)',
        padding: '2px 8px',
      }}
    >
      {live ? 'LIVE' : 'TEST'}
    </span>
  );
}
