'use client';

/**
 * SpanTree — renders the ordered list of Span rows extracted from
 * ActivityScreen.tsx:348-382 (normal sizing) and RunsScreen.tsx:269-303
 * (dense sizing, selected via the `dense` prop).
 *
 * CONTRACT (spec §1.1 + honesty rules §5):
 *   - Maps EXACTLY the `spans` array — never invents, filters, or reorders rows.
 *   - Returns null when spans.length === 0; the caller is responsible for any
 *     fallback text (spec: "caller owns fallback").
 *   - Uses SPAN_KIND_STYLE from the canonical map (deduped per T1).
 *
 * Props:
 *   spans  — the Span[] to render (pass item.spans or event.spans directly)
 *   dense  — true = RunsScreen compact sizing; false/omitted = ActivityScreen sizing
 */

import type { Span } from '@/lib/data/models';
import { SPAN_KIND_STYLE } from './spanKindStyle';

interface SpanTreeProps {
  spans: Span[];
  /** Use compact (RunsScreen) sizing when true. Default: false (ActivityScreen sizing). */
  dense?: boolean;
}

export function SpanTree({ spans, dense = false }: SpanTreeProps) {
  if (spans.length === 0) {
    return null;
  }

  // Sizing tokens: ActivityScreen vs RunsScreen
  const dotSize      = dense ? 6  : 8;
  const dotMarginTop = dense ? 5  : 6;
  const rowGap       = dense ? 9  : 12;
  const kindFontSize = 9;           // same in both
  const titleSize    = dense ? 12  : 13;
  const msFontSize   = dense ? 9.5 : 10.5;
  const detailSize   = dense ? 11  : 11.5;
  const colGap       = dense ? 9   : 12;
  const headerGap    = dense ? 7   : 8;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: rowGap }}>
      {spans.map((span, i) => {
        const kindColor = SPAN_KIND_STYLE[span.kind]?.color ?? '#8C877D';
        const kindBg    = SPAN_KIND_STYLE[span.kind]?.bg    ?? '#F1EFEA';

        return (
          <div key={i} style={{ display: 'flex', gap: colGap, alignItems: 'flex-start' }}>
            {/* dot */}
            <span
              style={{
                width:        dotSize,
                height:       dotSize,
                borderRadius: '50%',
                background:   kindColor,
                flex:         '0 0 auto',
                marginTop:    dotMarginTop,
              }}
            />

            {/* content column */}
            <div style={{ flex: 1, minWidth: 0 }}>
              {/* header row: kind badge · title · spacer · ms */}
              <div style={{ display: 'flex', alignItems: 'center', gap: headerGap, marginBottom: 3 }}>
                <span
                  style={{
                    fontSize:        kindFontSize,
                    fontWeight:      600,
                    color:           kindColor,
                    background:      kindBg,
                    padding:         '2px 6px',
                    borderRadius:    5,
                    textTransform:   'uppercase',
                  }}
                >
                  {span.kind}
                </span>
                <span style={{ fontSize: titleSize, fontWeight: 600, color: '#1A2E2B' }}>
                  {span.title}
                </span>
                <span style={{ flex: 1 }} />
                {span.ms != null && (
                  <span
                    style={{
                      fontFamily: "'IBM Plex Mono', monospace",
                      fontSize:   msFontSize,
                      color:      '#9BBFBB',
                      flex:       '0 0 auto',
                    }}
                  >
                    {span.ms}ms
                  </span>
                )}
              </div>

              {/* detail line */}
              <div
                style={{
                  fontFamily: "'IBM Plex Mono', monospace",
                  fontSize:   detailSize,
                  color:      '#5C7A76',
                  lineHeight: 1.5,
                }}
              >
                {span.detail}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
