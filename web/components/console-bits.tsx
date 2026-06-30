'use client';

/**
 * Shared presentational bits for the master/detail screens (Review queue +
 * Activity). Pure, token-driven, no data access — every color comes from the
 * design tokens (teal = automation, amber = human-in-the-loop) so the two
 * screens stay visually identical to the handoff. Channel/worker dots are
 * driven from the typed enum → color maps in `lib/tokens`.
 */
import type { CSSProperties, ReactNode } from 'react';
import type { ActionType, Channel } from '@/lib/data/models';

/** Human label for an action type (the small list-row "type tag"). */
export function typeLabel(type: ActionType): string {
  switch (type) {
    case 'OUTREACH':
      return 'Outreach';
    case 'COMMENT':
      return 'Reply';
    case 'DM':
      return 'DM';
    case 'POST':
      return 'Post';
    default:
      return type;
  }
}

/** Channel display name (dots carry the color; text avoids trademark logos). */
export function channelLabel(channel: Channel): string {
  switch (channel) {
    case 'GMAIL':
      return 'Gmail';
    case 'EMAIL':
      return 'Email';
    case 'SMS':
      return 'SMS';
    case 'INSTAGRAM':
    case 'IG':
      return 'Instagram';
    case 'REELS':
      return 'Reels';
    case 'TIKTOK':
      return 'TikTok';
    case 'FACEBOOK':
      return 'Facebook';
    default:
      return channel;
  }
}

/**
 * Plain-language statement of what approving this draft will DO — the operator's
 * "I'll post THIS on Instagram with this caption" framing. Channel-first because a
 * campaign draft is typed POST but the CHANNEL decides the verb (an EMAIL POST is
 * an email; an IG POST is a caption). Nothing here sends — it describes the staged
 * intent the Review queue holds for approval.
 */
export function actionIntent(
  type: ActionType,
  channel: Channel,
  target?: string | null,
): string {
  const to = target ? ` to ${target}` : '';
  switch (channel) {
    case 'GMAIL':
    case 'EMAIL':
      return type === 'COMMENT'
        ? `Will reply to this email${to}`
        : `Will send this email${to}`;
    case 'SMS':
      return `Will send this text message${to}`;
    case 'INSTAGRAM':
    case 'IG':
      return type === 'COMMENT'
        ? 'Will reply with this comment on Instagram'
        : type === 'DM'
          ? `Will send this Instagram DM${to}`
          : 'Will post this caption to Instagram';
    case 'REELS':
      return 'Will post this Reel caption to Instagram';
    case 'TIKTOK':
      return 'Will post this video caption to TikTok';
    case 'FACEBOOK':
      return type === 'COMMENT'
        ? 'Will reply with this comment on Facebook'
        : 'Will post this to Facebook';
    default:
      return `Will publish this ${typeLabel(type).toLowerCase()} to ${channelLabel(channel)}`;
  }
}

/** Maps the review filter chip ↔ the underlying action types. */
export type QueueFilter = 'ALL' | 'OUTREACH' | 'REPLIES' | 'POSTS';

export function matchesFilter(type: ActionType, filter: QueueFilter): boolean {
  switch (filter) {
    case 'ALL':
      return true;
    case 'OUTREACH':
      return type === 'OUTREACH';
    case 'REPLIES':
      return type === 'COMMENT' || type === 'DM';
    case 'POSTS':
      return type === 'POST';
    default:
      return true;
  }
}

/** HH:MM (UTC) extracted from an ISO timestamp — deterministic, no tz drift. */
export function clockTime(iso: string): string {
  const m = /T(\d{2}:\d{2})/.exec(iso);
  return m ? m[1] : iso;
}

/** A small uppercase mono tag (type tag / section label). */
export function Tag({ children, color }: { children: ReactNode; color?: string }) {
  return (
    <span
      className="label"
      style={{
        fontSize: 10,
        letterSpacing: '0.6px',
        color: color ?? 'var(--text-secondary)',
      }}
    >
      {children}
    </span>
  );
}

type ChipTone = 'amber' | 'teal' | 'success' | 'danger' | 'neutral';

const CHIP_TONE: Record<ChipTone, { text: string; bg: string; border: string }> = {
  amber: { text: 'var(--amber-text)', bg: 'var(--amber-bg)', border: 'var(--amber-border)' },
  teal: { text: 'var(--auto-chip-text)', bg: 'var(--auto-chip-bg)', border: 'var(--reasoning-border)' },
  success: { text: 'var(--success-text)', bg: 'var(--success-bg)', border: 'var(--success-dot)' },
  danger: { text: 'var(--danger-text)', bg: 'var(--danger-bg)', border: 'var(--danger-dot)' },
  neutral: { text: 'var(--text-secondary)', bg: 'var(--surface-alt)', border: 'var(--hairline)' },
};

/** A pill/chip in one of the semantic tones. */
export function Chip({
  tone = 'neutral',
  children,
  style,
}: {
  tone?: ChipTone;
  children: ReactNode;
  style?: CSSProperties;
}) {
  const c = CHIP_TONE[tone];
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
        fontSize: 11,
        fontWeight: 500,
        lineHeight: 1.6,
        padding: '1px 8px',
        borderRadius: 'var(--radius-chip)',
        color: c.text,
        background: c.bg,
        border: `1px solid ${c.border}`,
        whiteSpace: 'nowrap',
        ...style,
      }}
    >
      {children}
    </span>
  );
}

export type { ChipTone };

/**
 * Honest failure panel — renders the REAL provider error verbatim from an
 * action's `lastError` (e.g. a Meta/Graph `ig create media container failed:
 * HTTP 400 145 {"error":{"message": …}}` body). The send did NOT happen; we
 * surface WHY instead of a bare "Failed". The message is shown as-is
 * (whitespace preserved, monospace) — it is NEVER fabricated, paraphrased, or
 * prettified beyond wrapping; the operator reads the connector's own words.
 */
export function ProviderErrorPanel({
  error,
  title = 'Send failed — provider error',
}: {
  error: string;
  title?: string;
}) {
  return (
    <div
      role="alert"
      style={{
        border: '1px solid var(--danger-dot)',
        borderRadius: 'var(--radius-card)',
        background: 'var(--danger-bg)',
        padding: 'var(--pad-card)',
        display: 'grid',
        gap: 8,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span aria-hidden style={{ color: 'var(--danger-text)', fontWeight: 700 }}>
          ✕
        </span>
        <span
          className="label"
          style={{ color: 'var(--danger-text)', letterSpacing: '0.4px' }}
        >
          {title}
        </span>
      </div>
      <pre
        className="mono"
        style={{
          margin: 0,
          fontSize: 12,
          lineHeight: 1.5,
          color: 'var(--danger-text)',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          maxHeight: 260,
          overflow: 'auto',
        }}
      >
        {error}
      </pre>
      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        Real provider response — nothing was sent. Verbatim, not fabricated.
      </div>
    </div>
  );
}
