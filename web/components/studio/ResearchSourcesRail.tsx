'use client';

/**
 * ResearchSourcesRail — the "Deep research on the web" evidence panel.
 *
 * Renders REAL research sources (favicon + domain + title → real URL) pulled from
 * the researcher step's output. HONESTY: when no sources are present (Firecrawl key
 * absent, or the run-state JSON does not yet expose list_sources), it shows the
 * honest gated/empty state — it NEVER fabricates a citation. Each row links to the
 * real source URL.
 */
import type { ResearchSource } from '@/lib/studio/agency';
import { domainOf } from '@/lib/studio/agency';

const RESEARCHER_ACCENT = '#1D6FB8';

export function ResearchSourcesRail({
  sources,
  researchRan,
}: {
  sources: ResearchSource[];
  /** Whether a researcher step actually ran (drives the empty-state wording). */
  researchRan: boolean;
}) {
  return (
    <section
      aria-label="Deep research sources"
      style={{
        display: 'flex',
        flexDirection: 'column',
        background: '#fff',
        border: '1px solid var(--hairline)',
        borderRadius: 'var(--radius-card)',
        overflow: 'hidden',
        boxShadow: 'var(--shadow-card)',
      }}
    >
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '11px 14px',
          borderBottom: '1px solid var(--hairline)',
        }}
      >
        <span aria-hidden style={{ width: 7, height: 7, borderRadius: '50%', background: RESEARCHER_ACCENT }} />
        <h3 style={{ margin: 0, fontSize: 13, fontWeight: 590, color: 'var(--ink)' }}>
          Deep research
        </h3>
        <span style={{ flex: 1 }} />
        <span
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 11,
            color: 'var(--text-muted)',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          {sources.length > 0 ? `${sources.length} source${sources.length === 1 ? '' : 's'}` : '—'}
        </span>
      </header>

      <div style={{ padding: sources.length ? '8px' : '14px', display: 'flex', flexDirection: 'column', gap: 6 }}>
        {sources.length === 0 ? (
          <p style={{ margin: 0, fontSize: 12, lineHeight: 1.5, color: 'var(--text-muted)' }}>
            {researchRan
              ? 'The researcher ran, but no web citations are exposed in the run-state yet. Sources surface here once list_sources is included in the run JSON (or when a Firecrawl key is configured). Nothing is fabricated to fill this in.'
              : 'No deep-research step has run yet. When the strategist commissions web research, the cited sources (domain, title, URL) appear here — real citations only.'}
          </p>
        ) : (
          sources.map((s, i) => (
            <a
              key={`${s.url}_${i}`}
              href={s.url}
              target="_blank"
              rel="noreferrer noopener"
              className="spring-in"
              style={{
                display: 'flex',
                gap: 9,
                padding: '8px 9px',
                borderRadius: 8,
                textDecoration: 'none',
                background: 'var(--surface-alt)',
                border: '1px solid var(--hairline)',
                animationDelay: `${Math.min(i, 8) * 28}ms`,
              }}
            >
              <img
                src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(domainOf(s.url))}&sz=32`}
                alt=""
                width={16}
                height={16}
                style={{ borderRadius: 4, marginTop: 2, flex: '0 0 auto' }}
              />
              <span style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
                <span style={{ fontSize: 12.5, fontWeight: 560, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {s.title || domainOf(s.url)}
                </span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: RESEARCHER_ACCENT }}>
                  {domainOf(s.url)}
                </span>
                {s.snippet && (
                  <span style={{ fontSize: 11.5, lineHeight: 1.4, color: 'var(--text-muted)' }}>
                    {s.snippet.length > 120 ? `${s.snippet.slice(0, 120)}…` : s.snippet}
                  </span>
                )}
              </span>
            </a>
          ))
        )}
      </div>
    </section>
  );
}
