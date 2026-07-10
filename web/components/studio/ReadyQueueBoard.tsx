'use client';

import { useEffect, useState } from 'react';

/** One resolved media block of GET /studio/social/ready — real asset row or an
 * honest not-found; null when the draft referenced no asset at all. */
type ReadyMedia = {
  asset_id: string;
  found: boolean;
  media: 'image' | 'video' | null;
  tags: string[];
  caption: string | null;
  error?: string;
} | null;

/** One post package of GET /studio/social/ready. */
type ReadyPost = {
  action_id: string;
  channel: string;
  type: string | null;
  caption: string;
  target: string | null;
  run_id: string | null;
  created_at: string | null;
  scheduled_for: string | null;
  schedule_live: boolean;
  artwork: ReadyMedia;
  broll: ReadyMedia;
  publishable: boolean;
  blocked_reason: string | null;
};

const CHANNEL_BADGE: Record<string, string> = {
  instagram: '#7A5AF8',
  facebook: '#2563C9',
};

const MONO = "'IBM Plex Mono', monospace";

function preview(caption: string, n = 140): string {
  const flat = caption.replace(/\s+/g, ' ').trim();
  return flat.length > n ? `${flat.slice(0, n - 1)}…` : flat;
}

function scheduleLabel(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** Social Ready Queue — every pending IG/FB post package, complete and waiting at
 * the publish gate. Polls GET /studio/social/ready every 15s (FleetBoard pattern);
 * honest-empty: renders nothing when no post is pending. While Meta credentials
 * are missing, the panel shows the engine's exact blocked_reason — an approve
 * would refuse with the same words, so nothing here overpromises. */
export function ReadyQueueBoard() {
  const [posts, setPosts] = useState<ReadyPost[]>([]);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      fetch('/studio/social/ready')
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
        .then((d) => {
          if (alive) {
            setPosts(Array.isArray(d.posts) ? d.posts : []);
            setError(false);
          }
        })
        .catch(() => alive && setError(true));
    load();
    const t = setInterval(load, 15000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  if (error || posts.length === 0) return null;

  // The engine's own refusal reasons, verbatim (distinct — IG and FB name
  // different keys). Empty when every package is publishable.
  const blockedReasons = Array.from(
    new Set(posts.filter((p) => !p.publishable && p.blocked_reason).map((p) => p.blocked_reason as string)),
  );

  return (
    <div style={{ margin: '0 0 14px', border: '1px solid var(--border, #E5E1D8)', borderRadius: 9, padding: '10px 12px' }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', paddingBottom: 8 }}>
        <span style={{ fontSize: 10, fontFamily: MONO, color: '#A8A299', letterSpacing: '0.7px' }}>
          SOCIAL READY QUEUE — HELD AT PUBLISH GATE
        </span>
        <span style={{ fontSize: 10, fontFamily: MONO, color: '#A8A299' }}>
          {posts.length} post{posts.length === 1 ? '' : 's'} waiting
        </span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {posts.map((p) => (
          <div key={p.action_id} style={{ display: 'grid', gap: 3, minWidth: 0 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', minWidth: 0 }}>
              <span
                style={{
                  fontSize: 10,
                  fontFamily: MONO,
                  fontWeight: 600,
                  color: '#fff',
                  background: CHANNEL_BADGE[p.channel] ?? '#6B675F',
                  borderRadius: 4,
                  padding: '1px 6px',
                  letterSpacing: '0.5px',
                  flex: '0 0 auto',
                }}
              >
                {p.channel.toUpperCase()}
              </span>
              <span
                style={{
                  fontSize: 12,
                  color: 'var(--text, #38342C)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  minWidth: 0,
                }}
              >
                {preview(p.caption)}
              </span>
              {p.scheduled_for ? (
                <span
                  style={{
                    fontSize: 10,
                    fontFamily: MONO,
                    color: '#B45309',
                    border: '1px solid #B45309',
                    borderRadius: 999,
                    padding: '0 7px',
                    whiteSpace: 'nowrap',
                    flex: '0 0 auto',
                  }}
                >
                  scheduled {scheduleLabel(p.scheduled_for)}
                  {p.schedule_live ? ' · LIVE' : ''}
                </span>
              ) : null}
            </div>
            <div style={{ fontSize: 11, fontFamily: MONO, color: '#A8A299', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {p.artwork
                ? p.artwork.found
                  ? `artwork ${p.artwork.asset_id} · ${p.artwork.tags.length > 0 ? p.artwork.tags.join(', ') : 'no tags'} · ${p.artwork.media}`
                  : `artwork ${p.artwork.asset_id} · ${p.artwork.error ?? 'not found'}`
                : 'no artwork attached'}
              {p.broll ? ` · b-roll ${p.broll.asset_id}${p.broll.found && p.broll.media ? ` (${p.broll.media})` : ''}` : ''}
            </div>
          </div>
        ))}
      </div>
      {blockedReasons.length > 0 ? (
        <div
          role="note"
          style={{
            marginTop: 10,
            paddingTop: 8,
            borderTop: '1px solid var(--border, #E5E1D8)',
            fontSize: 11,
            fontFamily: MONO,
            color: '#B45309',
          }}
        >
          ● publish blocked — {blockedReasons.join(' · ')}
        </div>
      ) : null}
    </div>
  );
}
