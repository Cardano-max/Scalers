'use client';

/**
 * PostCampaignPreview — the per-artist IG/FB post-draft preview (P2 studio layer).
 *
 * Presentation over REAL staged data: one card per HELD post draft produced by the
 * engine drafter (studio.post_campaign.draft_studio_posts) — the caption in the
 * studio's brand voice, its hashtags + CTA, and the chosen ARTWORK with an
 * evidence-grounded "which artwork & why". Every card is badged HELD; NOTHING here
 * publishes (real IG/FB publishing is P4, behind Meta App Review + the existing
 * approve-first path).
 *
 * HONESTY: bound to real drafts only. The artwork block renders the studio asset's
 * own metadata (caption + matched style/motif tags + the grounded "why", traced to a
 * real asset id); the image is shown as a REFERENCE (resolved to the real picture on
 * publish), not a fabricated preview. When the artist has no artwork on file the card
 * says so plainly instead of inventing a picture.
 */
import { useMemo } from 'react';

const TEAL = '#0F8A82';

export interface PostArtwork {
  assetId: string;
  imageRef: string;
  caption: string;
  styles: string[];
  motifs: string[];
  matchedStyles: string[];
  matchedMotifs: string[];
  exactMatch: boolean;
  why: string;
}

export interface StudioPostDraft {
  platform: 'instagram' | 'facebook';
  actionId: string;
  held: boolean;
  /** Caption body (no hashtags). */
  caption: string;
  hashtags: string[];
  callToAction: string;
  /** Full rendered post text (body + CTA + hashtags), as staged in actions.draft. */
  draft: string;
  /** The chosen portfolio piece, or null when the artist has no artwork on file. */
  artwork: PostArtwork | null;
}

const PLATFORM_LABEL: Record<StudioPostDraft['platform'], string> = {
  instagram: 'INSTAGRAM',
  facebook: 'FACEBOOK',
};

export function PostCampaignPreview({
  artist,
  drafts,
}: {
  artist: string;
  drafts: StudioPostDraft[];
}) {
  const heldCount = useMemo(() => drafts.filter((d) => d.held).length, [drafts]);

  return (
    <section
      aria-label={`Post drafts for ${artist}`}
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
          Post drafts — {artist}
        </h3>
        <span style={{ flex: 1 }} />
        {drafts.length > 0 && (
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: 'var(--amber-text)',
              background: 'var(--amber-bg)',
              border: '1px solid var(--amber-border)',
              borderRadius: 'var(--radius-pill)',
              padding: '3px 9px',
              whiteSpace: 'nowrap',
            }}
          >
            {heldCount} HELD
          </span>
        )}
      </header>

      <p style={{ margin: 0, fontSize: 11.5, color: 'var(--text-muted)', lineHeight: 1.45 }}>
        Drafts only — nothing publishes. Instagram and Facebook posting goes live in a
        later phase; these stay held on the approve-first path.
      </p>

      {drafts.length === 0 ? (
        <div style={{ fontSize: 12.5, color: 'var(--text-muted)', padding: '8px 2px' }}>
          No staged posts yet.
        </div>
      ) : (
        drafts.map((d) => <DraftCard key={d.actionId} artist={artist} draft={d} />)
      )}
    </section>
  );
}

function DraftCard({ artist, draft }: { artist: string; draft: StudioPostDraft }) {
  return (
    <article
      data-action-id={draft.actionId}
      data-platform={draft.platform}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        border: '1px solid var(--hairline)',
        borderLeft: `3px solid ${TEAL}`,
        borderRadius: 10,
        padding: '10px 12px',
        background: '#fff',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span
          style={{
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: '0.02em',
            color: TEAL,
            background: 'var(--surface-alt)',
            border: '1px solid var(--hairline-strong)',
            borderRadius: 'var(--radius-pill)',
            padding: '2px 8px',
          }}
        >
          {PLATFORM_LABEL[draft.platform]}
        </span>
        {draft.held && (
          <span
            style={{
              fontSize: 10,
              fontWeight: 700,
              color: 'var(--amber-text)',
              background: 'var(--amber-bg)',
              border: '1px solid var(--amber-border)',
              borderRadius: 'var(--radius-pill)',
              padding: '2px 8px',
            }}
          >
            HELD
          </span>
        )}
      </div>

      {/* Caption body */}
      <div
        style={{
          fontSize: 12.5,
          lineHeight: 1.5,
          color: 'var(--text-secondary)',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {draft.caption || '(empty caption)'}
      </div>

      {/* CTA */}
      {draft.callToAction && (
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink)' }}>
          {draft.callToAction}
        </div>
      )}

      {/* Hashtags */}
      {draft.hashtags.length > 0 && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {draft.hashtags.map((h) => (
            <span
              key={h}
              style={{
                fontSize: 11,
                color: 'var(--teal-dark)',
                background: 'var(--surface-alt)',
                borderRadius: 'var(--radius-pill)',
                padding: '2px 7px',
              }}
            >
              #{h}
            </span>
          ))}
        </div>
      )}

      <ArtworkBlock artist={artist} artwork={draft.artwork} />
    </article>
  );
}

function ArtworkBlock({ artist, artwork }: { artist: string; artwork: PostArtwork | null }) {
  if (!artwork) {
    return (
      <div
        style={{
          borderTop: '1px solid var(--hairline)',
          paddingTop: 8,
          marginTop: 2,
          fontSize: 12,
          color: 'var(--text-muted)',
        }}
      >
        No artwork on file for {artist} yet — a piece is attached on approval.
      </div>
    );
  }

  const matched = [...artwork.matchedStyles, ...artwork.matchedMotifs];
  return (
    <div style={{ borderTop: '1px solid var(--hairline)', paddingTop: 8, marginTop: 2 }}>
      <div style={{ display: 'flex', gap: 10 }}>
        {/* Reference tile — honest: the picture is resolved on publish (P4), not shown
            as a fabricated preview here. */}
        <div
          aria-label="Artwork reference"
          style={{
            flex: '0 0 auto',
            width: 56,
            height: 56,
            borderRadius: 8,
            background: 'var(--surface-alt)',
            border: '1px dashed var(--hairline-strong)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 9,
            color: 'var(--text-muted)',
            textAlign: 'center',
            padding: 4,
          }}
        >
          ref
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink)' }}>
            {artwork.caption || '(untitled piece)'}
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <span
              style={{
                fontSize: 10,
                fontWeight: 600,
                color: artwork.exactMatch ? 'var(--success-text)' : 'var(--text-muted)',
                background: artwork.exactMatch ? 'var(--success-bg, #E7F5EE)' : 'var(--surface-alt)',
                borderRadius: 'var(--radius-pill)',
                padding: '2px 7px',
              }}
            >
              {artwork.exactMatch ? 'style match' : 'portfolio pick'}
            </span>
            {matched.map((t) => (
              <span
                key={t}
                style={{
                  fontSize: 10,
                  color: 'var(--teal-dark)',
                  background: 'var(--surface-alt)',
                  borderRadius: 'var(--radius-pill)',
                  padding: '2px 7px',
                }}
              >
                {t}
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* The grounded "why" — traces to the asset's own metadata + id. */}
      <p style={{ margin: '8px 0 0', fontSize: 11.5, color: 'var(--text-secondary)', lineHeight: 1.45 }}>
        {artwork.why}
      </p>
      <div style={{ marginTop: 4, fontSize: 10.5, color: 'var(--text-muted)', fontFamily: 'var(--font-mono, monospace)' }}>
        {artwork.imageRef} · asset {artwork.assetId}
      </div>
    </div>
  );
}
