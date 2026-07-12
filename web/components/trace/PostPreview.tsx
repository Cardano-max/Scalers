'use client';

/**
 * PostPreview — what a SOCIAL POST draft actually is, rendered as the operator will
 * publish it: the real image, the caption, and the evidence behind both.
 *
 * The review queue was built for OUTREACH. Every panel asked outreach questions — source
 * file, customer identity, offer — and a post has none of those, so an Instagram/Facebook
 * draft rendered as a wall of text under a red "this draft has no recorded evidence"
 * warning. Meanwhile the row itself carried a real artwork_asset_id, the VLM's reading of
 * the piece, six grounded hashtags, a CTA, and (now) the competitor post the operator
 * chose to mold. All of it was on file and none of it was on screen. An operator cannot
 * approve a post they cannot see.
 *
 * Two things this makes VERIFIABLE rather than trusted:
 *   - the image really attached to this draft (rendered from its own asset id), and
 *   - the competitor the operator PICKED is the one that was actually molded — handle,
 *     real score, the honest "why it worked", and a link to the original to compare.
 *
 * HONESTY: every field renders only from the draft's own stored context. A field that is
 * not on the row is simply absent — never a placeholder, never inferred.
 */
import { artifactRawUrl } from '@/lib/studio/artists';
import { ArtifactMedia } from '../studio/ArtifactMedia';
import { Chip } from '../console-bits';

export interface PostContext {
  artwork?: { assetId?: string | null; artifactId?: string | null; vlmSummary?: string | null } | null;
  artwork_asset_id?: string | null;
  broll_asset_id?: string | null;
  hashtags?: string[] | null;
  cta?: string | null;
  angle?: string | null;
  hook?: string | null;
  keywords?: string[] | null;
  artist?: string | null;
  competitor?: {
    postId?: string | null;
    handle?: string | null;
    url?: string | null;
    totalScore?: number | null;
    whyItWorked?: string | null;
    metrics?: Record<string, number> | null;
  } | null;
}

/** Parse the action's stored context JSON. Honest-null on anything unparseable. */
export function parsePostContext(context: string | null | undefined): PostContext | null {
  if (!context) return null;
  try {
    const parsed = JSON.parse(context);
    return parsed && typeof parsed === 'object' ? (parsed as PostContext) : null;
  } catch {
    return null; // a plain-text context is not a post context — say nothing
  }
}

/** True when this draft carries its OWN evidence (artwork and/or a molded competitor),
 *  so the outreach-shaped "no recorded evidence" alarm must not fire at it. */
export function hasPostEvidence(ctx: PostContext | null): boolean {
  if (!ctx) return false;
  return Boolean(ctx.artwork?.artifactId || ctx.artwork_asset_id || ctx.competitor?.handle);
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'grid', gap: 6 }}>
      <div className="label" style={{ fontSize: 10.5, color: 'var(--text-muted)' }}>{title}</div>
      {children}
    </div>
  );
}

export function PostPreview({
  context,
  caption,
  channel,
}: {
  context: string | null | undefined;
  caption: string;
  channel?: string | null;
}) {
  const ctx = parsePostContext(context);
  if (!ctx || !hasPostEvidence(ctx)) return null;

  const artifactId = ctx.artwork?.artifactId ?? null;
  const comp = ctx.competitor ?? null;
  const label = (channel || '').toLowerCase() === 'fb' ? 'Facebook' : 'Instagram';

  return (
    <section
      aria-label="Post preview"
      style={{
        border: '1px solid var(--hairline)',
        borderRadius: 'var(--radius-card)',
        background: 'var(--surface)',
        padding: '12px 14px',
        display: 'grid',
        gap: 12,
      }}
    >
      <div style={{ fontSize: 12.5, fontWeight: 700 }}>
        The post — what will actually go out on {label}
      </div>

      <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        {/* THE IMAGE. Rendered from the draft's OWN asset id — if it appears here, it is
            attached to this draft. Nothing else needs to be taken on trust. */}
        {artifactId ? (
          <div style={{ width: 190, flexShrink: 0 }}>
            <ArtifactMedia
              src={artifactRawUrl(artifactId)}
              alt={ctx.artwork?.vlmSummary || 'attached artwork'}
              height={190}
              controls={false}
            />
            {ctx.artwork?.vlmSummary ? (
              <div style={{ fontSize: 11, lineHeight: 1.45, color: 'var(--text-secondary)', marginTop: 6 }}>
                {ctx.artwork.vlmSummary}
              </div>
            ) : null}
          </div>
        ) : (
          <div style={{ fontSize: 12, fontStyle: 'italic', color: 'var(--text-muted)' }}>
            no image attached to this draft
          </div>
        )}

        <div style={{ flex: 1, minWidth: 240, display: 'grid', gap: 10 }}>
          <Section title="Caption">
            <div style={{ fontSize: 13, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>{caption}</div>
          </Section>

          {(ctx.hook || ctx.angle || ctx.cta) && (
            <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
              {ctx.hook ? (
                <Section title="Hook">
                  <div style={{ fontSize: 12.5 }}>{ctx.hook}</div>
                </Section>
              ) : null}
              {ctx.angle ? (
                <Section title="Angle">
                  <div style={{ fontSize: 12.5 }}>{ctx.angle}</div>
                </Section>
              ) : null}
              {ctx.cta ? (
                <Section title="CTA">
                  <div style={{ fontSize: 12.5 }}>{ctx.cta}</div>
                </Section>
              ) : null}
            </div>
          )}

          {ctx.hashtags?.length ? (
            <Section title={`Hashtags (${ctx.hashtags.length}) — from this piece's own tags`}>
              <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                {ctx.hashtags.map((h) => (
                  <Chip key={h}>#{h}</Chip>
                ))}
              </div>
            </Section>
          ) : null}
        </div>
      </div>

      {/* THE MOLD. The operator was asked to pick a competitor pattern; this is where they
          confirm the one they picked is the one that was used. Real handle, real score,
          honest reasoning, and a link to the original post to compare against. */}
      {comp?.handle ? (
        <div
          style={{
            borderTop: '1px solid var(--hairline)',
            paddingTop: 10,
            display: 'grid',
            gap: 6,
          }}
        >
          <div className="label" style={{ fontSize: 10.5, color: 'var(--text-muted)' }}>
            Molded from the competitor you picked
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
            <strong style={{ fontSize: 12.5 }}>@{comp.handle}</strong>
            {typeof comp.totalScore === 'number' ? (
              <Chip>score {comp.totalScore}</Chip>
            ) : null}
            {comp.metrics?.likes ? <Chip>{comp.metrics.likes} likes</Chip> : null}
            {comp.metrics?.comments ? <Chip>{comp.metrics.comments} comments</Chip> : null}
            {comp.url ? (
              <a
                href={comp.url}
                target="_blank"
                rel="noopener noreferrer"
                style={{ fontSize: 12, color: 'var(--accent, #0F8A82)' }}
              >
                open the original ↗
              </a>
            ) : null}
          </div>
          {comp.whyItWorked ? (
            <div style={{ fontSize: 11.5, lineHeight: 1.45, color: 'var(--text-secondary)' }}>
              {comp.whyItWorked}
            </div>
          ) : null}
          <div style={{ fontSize: 11, color: 'var(--text-muted)', fontStyle: 'italic' }}>
            Only the pattern&apos;s shape was reused — the wording, the artwork and the offer are ours.
          </div>
        </div>
      ) : null}
    </section>
  );
}
