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
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {posts.map((p) => (
          <PostCard key={p.action_id} post={p} />
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
function PostCard({ post: p }: { post: ReadyPost }) {
  const [imgFailed, setImgFailed] = useState(false);
  const { hook, body } = splitHook(p.caption);
  const a = p.anatomy;
  const badge = CHANNEL_BADGE[p.channel] ?? '#6B675F';
  const showImage = p.artwork?.found && p.artwork.media === 'image' && p.artwork.image_url && !imgFailed;

  return (
    <article
      style={{
        border: '1px solid var(--border, #E5E1D8)',
        borderRadius: 10,
        overflow: 'hidden',
        background: 'var(--surface, #FFFFFF)',
      }}
    >
      <div style={{ display: 'flex', gap: 12, padding: 10 }}>
        {/* The real image — the post's actual media, served from the library. */}
        <div style={{ flex: '0 0 116px', width: 116 }}>
          {showImage ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={p.artwork!.image_url as string}
              alt={p.artwork?.caption ?? 'post artwork'}
              onError={() => setImgFailed(true)}
              style={{ width: 116, height: 116, objectFit: 'cover', borderRadius: 8, display: 'block', background: '#F0EEE8' }}
            />
          ) : (
            <div
              style={{
                width: 116,
                height: 116,
                borderRadius: 8,
                background: '#F0EEE8',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                textAlign: 'center',
                fontSize: 10,
                fontFamily: MONO,
                color: '#A8A299',
                padding: 8,
              }}
            >
              {p.artwork && !p.artwork.found
                ? 'artwork no longer in library — pick a new one'
                : p.broll?.found
                  ? 'video post (b-roll)'
                  : 'no image attached'}
            </div>
          )}
        </div>

        {/* Caption + anatomy. */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', paddingBottom: 6 }}>
            <span
              style={{
                fontSize: 10,
                fontFamily: MONO,
                fontWeight: 600,
                color: '#fff',
                background: badge,
                borderRadius: 4,
                padding: '1px 6px',
                letterSpacing: '0.5px',
              }}
            >
              {p.channel.toUpperCase()}
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
                }}
              >
                scheduled {scheduleLabel(p.scheduled_for)}
                {p.schedule_live ? ' · LIVE' : ''}
              </span>
            ) : null}
          </div>

          {hook ? (
            <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text, #38342C)', lineHeight: 1.35 }}>
              {hook}
            </div>
          ) : null}
          {body ? (
            <div style={{ fontSize: 12, color: 'var(--muted, #6B675F)', lineHeight: 1.45, marginTop: 3, whiteSpace: 'pre-wrap' }}>
              {body}
            </div>
          ) : null}

          {/* Post anatomy — the pieces a marketer names: angle · CTA · keywords. */}
          {a && (a.angle || a.cta || a.hashtags.length > 0) ? (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
              {a.angle ? <Chip label="angle" value={a.angle.replace(/_/g, ' ')} /> : null}
              {a.cta ? <Chip label="CTA" value={a.cta} /> : null}
              {a.hashtags.slice(0, 6).map((h) => (
                <span
                  key={h}
                  style={{
                    fontSize: 10.5,
                    fontFamily: MONO,
                    color: '#7A5AF8',
                    background: 'rgba(122,90,248,0.08)',
                    borderRadius: 999,
                    padding: '1px 8px',
                  }}
                >
                  #{h}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </div>

      {/* The competitor mold — the story that makes a client say 'wow'. */}
      {p.mold && p.mold.handle ? (
        <div
          style={{
            borderTop: '1px dashed var(--border, #E5E1D8)',
            padding: '7px 10px',
            fontSize: 10.5,
            fontFamily: MONO,
            color: '#6B675F',
            background: 'rgba(15,138,130,0.05)',
          }}
        >
          <span style={{ color: '#0F8A82', fontWeight: 600 }}>molded from </span>
          {p.mold.url ? (
            <a href={p.mold.url} target="_blank" rel="noreferrer" style={{ color: '#0F8A82' }}>
              @{p.mold.handle}
            </a>
          ) : (
            <span style={{ color: '#0F8A82' }}>@{p.mold.handle}</span>
          )}
          {p.mold.structure.length > 0 ? (
            <span> · structure {p.mold.structure.join(' → ')}</span>
          ) : null}
          <span style={{ color: '#A8A299' }}> · shape only, never copied</span>
        </div>
      ) : null}
    </article>
  );
}
