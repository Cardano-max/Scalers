'use client';

import { useEffect, useState } from 'react';

/** One resolved media block of GET /studio/social/ready — real asset row or an
 * honest not-found; null when the draft referenced no asset at all. */
type ReadyMedia = {
  asset_id: string;
  artifact_id?: string | null;
  found: boolean;
  media: 'image' | 'video' | null;
  tags: string[];
  caption: string | null;
  /** /studio/artifacts/{id}/raw — the REAL bytes, so the card shows the picture. */
  image_url?: string | null;
  error?: string;
} | null;

/** The post anatomy the engine derived from the enriched draft context. */
type Anatomy = {
  hook: string | null;
  angle: string | null;
  cta: string | null;
  hashtags: string[];
} | null;

/** The competitor pattern this post was MOLDED from (structure only, never copied). */
type Mold = {
  handle: string | null;
  url: string | null;
  structure: string[];
  emotionalAngle: string | null;
  visualPattern: string | null;
  neverCopied: string | null;
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
  anatomy?: Anatomy;
  mold?: Mold;
  publishable: boolean;
  blocked_reason: string | null;
};

const CHANNEL_BADGE: Record<string, string> = {
  instagram: '#7A5AF8',
  facebook: '#2563C9',
};

const MONO = "'IBM Plex Mono', monospace";

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

/** Split a caption into its hook (first non-empty line) and the body remainder,
 *  so the card leads with the hook the way a real feed post does. */
function splitHook(caption: string): { hook: string; body: string } {
  const lines = caption.split('\n');
  let i = 0;
  while (i < lines.length && !lines[i].trim()) i += 1;
  const hook = (lines[i] ?? '').trim();
  const body = lines
    .slice(i + 1)
    .join('\n')
    .trim();
  return { hook, body };
}

/** A labelled anatomy chip (angle · CTA · a keyword). */
function Chip({ label, value }: { label: string; value: string }) {
  return (
    <span
      style={{
        display: 'inline-flex',
        gap: 5,
        alignItems: 'baseline',
        fontSize: 10.5,
        fontFamily: MONO,
        border: '1px solid var(--border, #E5E1D8)',
        borderRadius: 999,
        padding: '1px 8px',
        color: 'var(--text, #38342C)',
        background: 'var(--surface, #FBFAF7)',
      }}
    >
      <span style={{ color: '#A8A299', letterSpacing: '0.4px' }}>{label}</span>
      <span style={{ fontWeight: 600 }}>{value}</span>
    </span>
  );
}

/** Social Ready Queue — every pending IG/FB post rendered as the REAL post the
 * operator is approving: the image, the hook-led caption, the post anatomy
 * (angle · CTA · keywords) and the competitor pattern it was molded from
 * (structure only, never copied). Polls GET /studio/social/ready every 15s;
 * honest-empty (renders nothing) when no post is pending. While Meta credentials
 * are missing, each card shows the engine's exact blocked_reason. */
export function ReadyQueueBoard({ onOpen }: { onOpen?: (actionId: string) => void } = {}) {
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

  const blockedReasons = Array.from(
    new Set(posts.filter((p) => !p.publishable && p.blocked_reason).map((p) => p.blocked_reason as string)),
  );

  return (
    <div style={{ margin: '0 0 14px', border: '1px solid var(--border, #E5E1D8)', borderRadius: 9, padding: '10px 12px' }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', paddingBottom: 10 }}>
        <span style={{ fontSize: 10, fontFamily: MONO, color: '#A8A299', letterSpacing: '0.7px' }}>
          POSTS READY TO PUBLISH
        </span>
        <span style={{ fontSize: 10, fontFamily: MONO, color: '#A8A299' }}>
          {posts.length} post{posts.length === 1 ? '' : 's'} waiting
        </span>
      </div>
      {/* BOUNDED. This panel sits ABOVE the draft list in the same column, so any height it
          takes it takes from the list. Unbounded, two posts hid five Gmail drafts entirely.
          It scrolls within its own box now — the list below is always reachable. */}
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
          maxHeight: 190,
          overflowY: 'auto',
        }}
      >
        {posts.map((p) => (
          <PostCard key={p.action_id} post={p} onOpen={onOpen} />
        ))}
      </div>
      {blockedReasons.length > 0 ? (
        <div
          role="note"
          style={{
            marginTop: 12,
            paddingTop: 8,
            borderTop: '1px solid var(--border, #E5E1D8)',
            fontSize: 11.5,
            color: '#B45309',
          }}
        >
          ● Connect the studio&rsquo;s Instagram / Facebook to publish — a one-time
          setup. These posts stay safely held until then.
          <details style={{ marginTop: 4 }}>
            <summary style={{ cursor: 'pointer', fontSize: 10.5, color: '#A8A299' }}>
              Technical detail
            </summary>
            <div style={{ fontFamily: MONO, fontSize: 10.5, marginTop: 4, color: '#A8A299', whiteSpace: 'pre-wrap' }}>
              {blockedReasons.join('\n')}
            </div>
          </details>
        </div>
      ) : null}
    </div>
  );
}

/** One post rendered as a real feed card: image + hook-led caption + anatomy +
 *  the competitor mold. Everything is a live field — nothing is invented. */
function PostCard({ post: p, onOpen }: { post: ReadyPost; onOpen?: (actionId: string) => void }) {
  const [imgFailed, setImgFailed] = useState(false);
  const { hook, body } = splitHook(p.caption);
  const a = p.anatomy;
  const badge = CHANNEL_BADGE[p.channel] ?? '#6B675F';
  const showImage = p.artwork?.found && p.artwork.media === 'image' && p.artwork.image_url && !imgFailed;

  // THE CARD MUST OPEN ITS DRAFT.
  // This is the biggest, most image-rich thing in the review queue — the operator's eye goes
  // straight to it and they click it to inspect the post. It was an inert <article>: nothing
  // happened, and the detail pane sat on "No action selected — pick a row", so the post read
  // as un-openable. The draft it belongs to was one scroll further down the list the whole
  // time. Clicking the card now selects that exact draft (switching the filter if the post is
  // hidden behind the current one) and opens the full review panel.
  const open = onOpen ? () => onOpen(p.action_id) : undefined;

  return (
    <article
      onClick={open}
      onKeyDown={
        open
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                open();
              }
            }
          : undefined
      }
      role={open ? 'button' : undefined}
      tabIndex={open ? 0 : undefined}
      aria-label={open ? `Open the ${p.channel} post draft for review` : undefined}
      title={open ? 'Open this post for review' : undefined}
      style={{
        border: '1px solid var(--border, #E5E1D8)',
        borderRadius: 10,
        overflow: 'hidden',
        background: 'var(--surface, #FFFFFF)',
        cursor: open ? 'pointer' : undefined,
      }}
    >
      {/* COMPACT CARD — a summary, not the whole post.
          This panel used to render each post's full caption, its angle/CTA chips, all six
          hashtags and the competitor-mold block. Two posts were enough to push the ACTUAL
          draft list — the five Gmail drafts — clean off the bottom of the screen: the
          operator saw "7 drafts waiting", scrolled, and found only the two posts. The full
          post now lives in the detail panel (which this card opens), so the card only has
          to be recognisable: thumbnail, channel, one line. */}
      <div style={{ display: 'flex', gap: 10, padding: 9, alignItems: 'center' }}>
        <div style={{ flex: '0 0 52px', width: 52 }}>
          {showImage ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={p.artwork!.image_url as string}
              alt={p.artwork?.caption ?? 'post artwork'}
              onError={() => setImgFailed(true)}
              style={{ width: 52, height: 52, objectFit: 'cover', borderRadius: 6, display: 'block', background: '#F0EEE8' }}
            />
          ) : (
            <div
              style={{
                width: 52,
                height: 52,
                borderRadius: 6,
                background: '#F0EEE8',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                textAlign: 'center',
                fontSize: 8.5,
                fontFamily: MONO,
                color: '#A8A299',
                padding: 4,
                lineHeight: 1.2,
              }}
            >
              {p.artwork && !p.artwork.found ? 'no artwork' : p.broll?.found ? 'video' : 'no image'}
            </div>
          )}
        </div>

        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', gap: 7, alignItems: 'center', paddingBottom: 3 }}>
            <span
              style={{
                fontSize: 9.5,
                fontFamily: MONO,
                fontWeight: 600,
                color: '#fff',
                background: badge,
                borderRadius: 4,
                padding: '1px 5px',
                letterSpacing: '0.5px',
              }}
            >
              {p.channel.toUpperCase()}
            </span>
            {p.mold?.handle ? (
              <span style={{ fontSize: 9.5, fontFamily: MONO, color: '#0F8A82' }}>
                molded from @{p.mold.handle}
              </span>
            ) : null}
            {p.scheduled_for ? (
              <span style={{ fontSize: 9.5, fontFamily: MONO, color: '#B45309' }}>
                scheduled {scheduleLabel(p.scheduled_for)}
                {p.schedule_live ? ' · LIVE' : ''}
              </span>
            ) : null}
          </div>
          <div
            style={{
              fontSize: 12.5,
              fontWeight: 600,
              color: 'var(--text, #38342C)',
              lineHeight: 1.3,
              overflow: 'hidden',
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
            }}
          >
            {hook || body || 'post draft'}
          </div>
        </div>
      </div>

    </article>
  );
}
