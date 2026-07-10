/**
 * artists.ts — typed client for the engine's artist roster / profile / artwork
 * surface (spec sections 4 & 20/22).
 *
 * All calls are SAME-ORIGIN `/studio/*` paths: next.config rewrites proxy them to
 * the engine (STUDIO_BACKEND_ORIGIN), so no CORS and any dev port works.
 *
 * ENGINE CONTRACT (built in parallel — these shapes are the agreed contract):
 *   GET  /studio/artists                     -> { artists: ArtistSummary[] }
 *   GET  /studio/artists/{slug}              -> { artist: ArtistDetail }
 *   POST /studio/artists/{slug}/memory       -> { ok, memoryId }   body {text}
 *   GET  /studio/artifacts?kind=&artist=     -> { artifacts: ContextArtifact[] }
 *   GET  /studio/artifacts/{id}/raw          -> image bytes (usable as <img src>)
 *   POST /studio/upload/image                -> JSON {name, contentBase64, artist, prompt}
 *
 * HONESTY: every accessor is defensive — a missing endpoint (404 while the engine
 * side lands), a transport error, or a malformed body surfaces as a thrown Error
 * the UI renders as an honest error/empty state. Nothing is fabricated here.
 */

export interface ArtistSummary {
  slug: string;
  name: string;
  studios: string[];
  artworkCount: number;
  campaignCount: number;
  memoryCount: number;
}

export interface ArtistArtwork {
  assetId: string;
  artifactId: string;
  styles: string[];
  motifs: string[];
  vlmSummary: string | null;
  /** Why analysis is missing, when it is (engine's concrete reason). */
  vlmError?: string | null;
}

export interface ArtistCampaign {
  campaign_name: string;
  offer_price_usd: number | null;
  message_copy: string | null;
  cta: string | null;
  sent_at: string | null;
  delivered_count: number | null;
  failed_count: number | null;
  dnd_blocked_count: number | null;
}

export interface ArtistMemory {
  at: string;
  text: string;
}

export interface ArtistDetail {
  slug: string;
  name: string;
  email: string | null;
  phone: string | null;
  studios: string[];
  styleTags: string[];
  artworks: ArtistArtwork[];
  campaigns: ArtistCampaign[];
  memories: ArtistMemory[];
}

export interface ContextArtifact {
  id: string;
  kind: string;
  name: string;
  createdAt: string | null;
  artist: string | null;
  vlmStatus: string | null;
  hasPreview: boolean;
}

export interface UploadImageResult {
  ok: boolean;
  id?: string;
  name?: string;
  /** VLM description when the engine captured one (honest-null otherwise). */
  vlmSummary?: string | null;
  /** e.g. 'ok' | 'unavailable' | 'pending' — the engine's honest VLM state. */
  vlmStatus?: string | null;
  /** The engine's concrete reason when analysis was skipped/failed (e.g. a
   *  missing model key) — shown to the operator so recovery is obvious. */
  vlmError?: string | null;
  /** The engine's own honest note about what was (and was not) captured. */
  note?: string | null;
  error?: string;
}

const strArr = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];
const str = (v: unknown): string | null => (typeof v === 'string' && v.length > 0 ? v : null);
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { method: 'GET', headers: { accept: 'application/json' }, signal });
  if (!res.ok) throw new Error(`HTTP ${res.status} from ${url.split('?')[0]}`);
  return (await res.json()) as T;
}

/** Roster: GET /studio/artists. Throws on transport / missing endpoint. */
export async function fetchArtists(signal?: AbortSignal): Promise<ArtistSummary[]> {
  const d = await getJson<{ artists?: unknown }>('/studio/artists', signal);
  if (!Array.isArray(d.artists)) return [];
  return (d.artists as Array<Record<string, unknown>>).map((a) => ({
    slug: str(a.slug) ?? '',
    name: str(a.name) ?? str(a.slug) ?? 'Unknown artist',
    studios: strArr(a.studios),
    artworkCount: num(a.artworkCount) ?? 0,
    campaignCount: num(a.campaignCount) ?? 0,
    memoryCount: num(a.memoryCount) ?? 0,
  })).filter((a) => a.slug.length > 0);
}

/** Profile: GET /studio/artists/{slug}. */
export async function fetchArtist(slug: string, signal?: AbortSignal): Promise<ArtistDetail> {
  const d = await getJson<{ artist?: Record<string, unknown> }>(
    `/studio/artists/${encodeURIComponent(slug)}`,
    signal,
  );
  const a = d.artist;
  if (!a || typeof a !== 'object') throw new Error('artist payload missing');
  const artworks = Array.isArray(a.artworks) ? (a.artworks as Array<Record<string, unknown>>) : [];
  const campaigns = Array.isArray(a.campaigns) ? (a.campaigns as Array<Record<string, unknown>>) : [];
  const memories = Array.isArray(a.memories) ? (a.memories as Array<Record<string, unknown>>) : [];
  return {
    slug: str(a.slug) ?? slug,
    name: str(a.name) ?? slug,
    email: str(a.email),
    phone: str(a.phone),
    studios: strArr(a.studios),
    styleTags: strArr(a.styleTags),
    artworks: artworks.map((w) => ({
      assetId: str(w.assetId) ?? '',
      artifactId: str(w.artifactId) ?? '',
      styles: strArr(w.styles),
      motifs: strArr(w.motifs),
      vlmSummary: str(w.vlmSummary),
      vlmError: str(w.vlmError),
    })),
    campaigns: campaigns.map((c) => ({
      // engine serves the key as `name`; accept the contract's `campaign_name` too
      campaign_name: str(c.campaign_name) ?? str(c.name) ?? 'Untitled campaign',
      offer_price_usd: num(c.offer_price_usd),
      message_copy: str(c.message_copy),
      cta: str(c.cta),
      sent_at: str(c.sent_at),
      delivered_count: num(c.delivered_count),
      failed_count: num(c.failed_count),
      dnd_blocked_count: num(c.dnd_blocked_count),
    })),
    memories: memories
      .map((m) => ({ at: str(m.at) ?? '', text: str(m.text) ?? '' }))
      .filter((m) => m.text.length > 0),
  };
}

/** POST /studio/artists/{slug}/memory {text} -> {ok, memoryId}. */
export async function addArtistMemory(
  slug: string,
  text: string,
): Promise<{ ok: boolean; memoryId?: string }> {
  const res = await fetch(`/studio/artists/${encodeURIComponent(slug)}/memory`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  const data = (await res.json().catch(() => ({}))) as {
    ok?: boolean;
    memoryId?: string;
    error?: string;
  };
  if (!res.ok || !data.ok) {
    throw new Error(data.error || `memory save failed (HTTP ${res.status})`);
  }
  return { ok: true, memoryId: data.memoryId };
}

/** GET /studio/artifacts?kind=&artist= — the universal context-artifact registry. */
export async function fetchArtifacts(
  filter: { kind?: string; artist?: string } = {},
  signal?: AbortSignal,
): Promise<ContextArtifact[]> {
  const params = new URLSearchParams();
  if (filter.kind) params.set('kind', filter.kind);
  if (filter.artist) params.set('artist', filter.artist);
  const qs = params.toString();
  const d = await getJson<{ artifacts?: unknown }>(
    `/studio/artifacts${qs ? `?${qs}` : ''}`,
    signal,
  );
  if (!Array.isArray(d.artifacts)) return [];
  return (d.artifacts as Array<Record<string, unknown>>).map((a) => ({
    id: str(a.id) ?? '',
    kind: str(a.kind) ?? 'unknown',
    name: str(a.name) ?? 'Untitled',
    createdAt: str(a.createdAt),
    artist: str(a.artist),
    vlmStatus: str(a.vlmStatus),
    hasPreview: a.hasPreview === true,
  })).filter((a) => a.id.length > 0);
}

/** The raw image URL for an artifact — usable directly as an <img src>. */
export function artifactRawUrl(artifactId: string): string {
  return `/studio/artifacts/${encodeURIComponent(artifactId)}/raw`;
}

/**
 * Upload one artwork image: POST /studio/upload/image with JSON
 * {name, contentBase64, artist, prompt} (+ mediaType for the current engine).
 * Returns the engine's HONEST result — a VLM summary only when the engine
 * really produced one; otherwise its own status/note.
 */
export async function uploadArtworkImage(args: {
  name: string;
  contentBase64: string;
  mediaType?: string;
  artist: string;
  prompt?: string;
}): Promise<UploadImageResult> {
  const res = await fetch('/studio/upload/image', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      name: args.name,
      contentBase64: args.contentBase64,
      ...(args.mediaType ? { mediaType: args.mediaType } : {}),
      artist: args.artist,
      ...(args.prompt ? { prompt: args.prompt } : {}),
    }),
  });
  const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (!res.ok || data.ok !== true) {
    return {
      ok: false,
      error:
        (typeof data.error === 'string' && data.error) || `upload failed (HTTP ${res.status})`,
    };
  }
  return {
    ok: true,
    id: str(data.id) ?? undefined,
    name: str(data.name) ?? undefined,
    vlmSummary: str(data.vlmSummary),
    vlmStatus: str(data.vlmStatus),
    vlmError: str(data.vlmError),
    note: str(data.note),
  };
}
