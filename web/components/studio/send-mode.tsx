'use client';

/**
 * Shared send-mode UI — the explicit Live vs Test/redirect control + badge used by
 * every operator send surface (campaign send-eligible, the Review Queue, and the
 * studio staged-drafts review).
 *
 * Default is Test (safe): a send is rerouted to the operator inbox with a [TEST] marker
 * and NO real recipient is contacted. Live is the EXPLICIT operator authorization that
 * lets a send reach the real recipient with a clean subject — so flipping to Live is a
 * real-send decision and is gated behind an inline confirm. The copy says plainly what
 * each mode does so the [TEST] marker is understood, never mistaken for a bug.
 */
import { useState } from 'react';
import type { SendMode } from '@/lib/studio/campaign-send';

const TEAL = '#0F8A82';

/** A small pill badging the resolved send mode — Live (real recipient, clean) vs Test
 *  (rerouted to the operator inbox with a [TEST] marker). Honest: it reflects the mode
 *  the engine actually reported, never an assumed one. */
export function ModeBadge({ mode }: { mode: SendMode }) {
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

/**
 * The Test/Live segmented toggle + the plain-language caption. Switching to Test takes
 * effect immediately (it is the safe direction); switching to Live opens an inline
 * confirm first (it authorizes a real send). The active mode is badged.
 */
export function SendModeToggle({
  live,
  onChange,
  disabled = false,
  disabledReason,
}: {
  live: boolean;
  onChange: (live: boolean) => void;
  disabled?: boolean;
  /** Plain-language tooltip explaining WHY the toggle is disabled (e.g. "Live
   *  sending unlocks after test-mode sign-off") — a disabled control must never
   *  be a mystery. */
  disabledReason?: string;
}) {
  const [confirming, setConfirming] = useState(false);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div role="group" aria-label="Send mode" style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--text-secondary)' }}>Mode</span>
        <div
          title={disabled ? disabledReason : undefined}
          style={{
            display: 'inline-flex',
            border: '1px solid var(--hairline-strong)',
            borderRadius: 'var(--radius-pill)',
            overflow: 'hidden',
            opacity: disabled ? 0.6 : 1,
          }}
        >
          <button
            type="button"
            aria-pressed={!live}
            disabled={disabled}
            onClick={() => {
              setConfirming(false);
              onChange(false);
            }}
            style={{
              fontSize: 11.5,
              fontWeight: 600,
              padding: '4px 12px',
              border: 'none',
              cursor: disabled ? 'not-allowed' : 'pointer',
              color: !live ? '#fff' : 'var(--text-secondary)',
              background: !live ? TEAL : '#fff',
            }}
          >
            Test (safe)
          </button>
          <button
            type="button"
            aria-pressed={live}
            disabled={disabled}
            title={disabled ? disabledReason : undefined}
            onClick={() => {
              // Switching INTO live authorizes a real send — gate it behind a confirm.
              if (!live) setConfirming(true);
            }}
            style={{
              fontSize: 11.5,
              fontWeight: 600,
              padding: '4px 12px',
              border: 'none',
              borderLeft: '1px solid var(--hairline-strong)',
              cursor: disabled ? 'not-allowed' : 'pointer',
              color: live ? '#fff' : 'var(--text-secondary)',
              background: live ? 'var(--danger-text)' : '#fff',
            }}
          >
            Live
          </button>
        </div>
        <ModeBadge mode={live ? 'live' : 'test_redirect'} />
      </div>

      {confirming && !live && (
        <div
          role="group"
          aria-label="Confirm live mode"
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            background: 'var(--danger-bg)',
            border: '1px solid #F1BEB8',
            borderRadius: 9,
            padding: 10,
          }}
        >
          <p style={{ margin: 0, fontSize: 11.5, color: 'var(--danger-text)', lineHeight: 1.5 }}>
            Live sends a REAL email to the actual recipient with a clean subject. Only switch
            to Live when you intend real outreach. Continue?
          </p>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              type="button"
              onClick={() => {
                onChange(true);
                setConfirming(false);
              }}
              style={{
                fontSize: 12,
                fontWeight: 600,
                color: '#fff',
                background: 'var(--danger-text)',
                border: 'none',
                borderRadius: 'var(--radius-button)',
                padding: '7px 14px',
                cursor: 'pointer',
              }}
            >
              Enable Live
            </button>
            <button
              type="button"
              onClick={() => setConfirming(false)}
              style={{
                fontSize: 12,
                fontWeight: 600,
                color: 'var(--text-secondary)',
                background: '#fff',
                border: '1px solid var(--hairline-strong)',
                borderRadius: 'var(--radius-button)',
                padding: '7px 14px',
                cursor: 'pointer',
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      <p style={{ margin: 0, fontSize: 11, color: live ? 'var(--danger-text)' : 'var(--text-muted)', lineHeight: 1.45 }}>
        {live
          ? 'Live mode: sends go to the real recipient with a clean subject. Switch to Test to route them safely to your inbox.'
          : 'Test mode: sends go to your inbox tagged [TEST]. Switch to Live to send to real recipients.'}
      </p>
    </div>
  );
}
