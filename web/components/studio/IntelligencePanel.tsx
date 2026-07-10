'use client';

/**
 * IntelligencePanel — the executive brain on the Overview tab.
 *
 * Renders GET /studio/intelligence: evidence-backed recommendations first
 * (what to run next and WHY, every line aggregated from real rows), then the
 * best real campaigns, the objection landscape from real analyst reads, and
 * artist library depth. Honest-empty: sections with no data simply don't
 * render — nothing is ever padded.
 */
import { useEffect, useState } from 'react';

type Intelligence = {
  bestCampaigns: {
    campaign_name: string; artist_name: string | null; cta: string | null;
    recipient_count: number | null; delivered_count: number | null;
    delivery_rate: number | null;
  }[];
  objections: { objection: string; leads: number }[];
  artists: { artist: string; pieces: number; videos: number }[];
  recommendations: { recommend: string; why: string }[];
};

const MONO = "'IBM Plex Mono', monospace";

export function IntelligencePanel() {
  const [data, setData] = useState<Intelligence | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    fetch('/studio/intelligence')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d) => alive && setData(d))
      .catch(() => alive && setError(true));
    return () => {
      alive = false;
    };
  }, []);

  if (error || !data) return null;

  return (
    <section
      aria-label="Campaign intelligence"
      style={{
        border: '1px solid var(--hairline, #E5E1D8)',
        borderRadius: 10,
        background: 'var(--surface, #fff)',
        padding: '14px 16px',
        marginBottom: 16,
        display: 'grid',
        gap: 10,
      }}
    >
      <div style={{ fontSize: 10, fontFamily: MONO, color: '#A8A299', letterSpacing: '0.7px' }}>
        CAMPAIGN INTELLIGENCE — every number from real rows
      </div>

      {data.recommendations.length > 0 && (
        <div style={{ display: 'grid', gap: 6 }}>
          {data.recommendations.map((r, i) => (
            <div key={i} style={{ fontSize: 12.5, lineHeight: 1.45 }}>
              <span style={{ fontWeight: 700, color: '#0F8A82' }}>→ {r.recommend}</span>
              <span style={{ color: 'var(--text-muted, #7A756C)' }}> — {r.why}</span>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap' }}>
        {data.bestCampaigns.length > 0 && (
          <div style={{ minWidth: 220 }}>
            <div style={{ fontSize: 10, fontFamily: MONO, color: '#A8A299', paddingBottom: 4 }}>
              BEST CAMPAIGNS (delivered)
            </div>
            {data.bestCampaigns.slice(0, 3).map((c) => (
              <div key={c.campaign_name} style={{ fontSize: 12 }}>
                {c.campaign_name} · {c.delivered_count ?? '—'}/{c.recipient_count ?? '—'}
                {c.delivery_rate != null ? ` (${Math.round(c.delivery_rate * 100)}%)` : ''}
              </div>
            ))}
          </div>
        )}
        {data.objections.length > 0 && (
          <div style={{ minWidth: 160 }}>
            <div style={{ fontSize: 10, fontFamily: MONO, color: '#A8A299', paddingBottom: 4 }}>
              OBJECTION LANDSCAPE
            </div>
            {data.objections.slice(0, 4).map((o) => (
              <div key={o.objection} style={{ fontSize: 12 }}>
                {o.objection}: {o.leads} lead{o.leads === 1 ? '' : 's'}
              </div>
            ))}
          </div>
        )}
        {data.artists.length > 0 && (
          <div style={{ minWidth: 160 }}>
            <div style={{ fontSize: 10, fontFamily: MONO, color: '#A8A299', paddingBottom: 4 }}>
              ARTIST LIBRARY
            </div>
            {data.artists.slice(0, 4).map((a) => (
              <div key={a.artist} style={{ fontSize: 12 }}>
                {a.artist}: {a.pieces} piece{a.pieces === 1 ? '' : 's'}
                {a.videos > 0 ? ` · ${a.videos} video` : ''}
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
