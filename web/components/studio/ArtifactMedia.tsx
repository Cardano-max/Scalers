'use client';

/**
 * ArtifactMedia — renders an artifact's stored bytes (GET /studio/artifacts/{id}/raw)
 * as the RIGHT media element. The list APIs don't carry a media type, so:
 *   - a name with a video extension (or an explicit isVideo hint) renders a
 *     <video controls> directly;
 *   - otherwise we try <img>; if the bytes aren't an image (a video artifact
 *     used to render as a broken image), we fall back to <video controls>;
 *   - if the video can't play either, an honest "preview unavailable" block.
 * Nothing is fabricated — it is the same raw URL either way.
 */
import { useState } from 'react';

const VIDEO_EXT = /\.(mp4|mov|m4v|webm|ogv)$/i;

export function looksLikeVideoName(name?: string | null): boolean {
  return !!name && VIDEO_EXT.test(name.trim());
}

export function ArtifactMedia({
  src,
  alt,
  name,
  isVideo,
  height = 110,
  controls = true,
}: {
  /** The raw-bytes URL (artifactRawUrl(id)). */
  src: string;
  alt: string;
  /** Original file name when known — used only to pick <video> up front. */
  name?: string | null;
  /** Explicit hint when the caller knows the media type (e.g. ready-queue rows). */
  isVideo?: boolean;
  height?: number;
  /** false for decorative previews inside clickable cards (first frame only). */
  controls?: boolean;
}) {
  const startAsVideo = isVideo === true || looksLikeVideoName(name);
  const [stage, setStage] = useState<'image' | 'video' | 'failed'>(
    startAsVideo ? 'video' : 'image',
  );

  if (stage === 'failed') {
    return (
      <div
        style={{
          height,
          display: 'grid',
          placeItems: 'center',
          background: 'var(--surface-alt)',
          color: 'var(--text-faint)',
          fontSize: 11,
          padding: 8,
          textAlign: 'center',
        }}
      >
        preview unavailable
      </div>
    );
  }

  if (stage === 'video') {
    return (
      // eslint-disable-next-line jsx-a11y/media-has-caption -- engine-served b-roll bytes; no caption track exists
      <video
        src={src}
        controls={controls}
        preload="metadata"
        muted
        playsInline
        aria-label={alt}
        onError={() => setStage('failed')}
        style={{ width: '100%', height, objectFit: 'cover', display: 'block', background: '#000' }}
      />
    );
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element -- engine-served bytes; no optimizer configured
    <img
      src={src}
      alt={alt}
      loading="lazy"
      // Bytes that aren't an image (a video artifact) fall back to <video>.
      onError={() => setStage('video')}
      style={{ width: '100%', height, objectFit: 'cover', display: 'block', background: 'var(--surface-alt)' }}
    />
  );
}
