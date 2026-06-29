'use client';

/**
 * 64px top bar: page title + subtitle (left); client pill + "LangGraph · live"
 * pill (right). The title/subtitle are derived from the active screen.
 */
import { useConsole, type ScreenId } from '@/state/console-store';
import { Dot } from './icons';

const TITLES: Record<ScreenId, { title: string; subtitle: string }> = {
  overview: { title: 'Overview', subtitle: 'Autonomy, deliverability, and what needs you' },
  review: { title: 'Review queue', subtitle: 'Actions the engine escalated for your sign-off' },
  activity: { title: 'Activity', subtitle: 'What the agents executed — and why' },
  feed: { title: 'Live feed', subtitle: 'Realtime decision stream' },
  runs: { title: 'Runs', subtitle: 'LangGraph / Temporal workflow history' },
  command: { title: 'Command', subtitle: 'Steer the harness in natural language' },
};

export function TopBar({
  clientName,
  pack,
}: {
  clientName: string;
  pack: string;
}) {
  const { screen } = useConsole();
  const { title, subtitle } = TITLES[screen];

  return (
    <header
      style={{
        height: 64,
        minHeight: 64,
        borderBottom: '1px solid var(--hairline)',
        background: 'var(--canvas)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 24px',
      }}
    >
      <div style={{ lineHeight: 1.2 }}>
        <div style={{ fontSize: 19, fontWeight: 600 }}>{title}</div>
        <div style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>{subtitle}</div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 8,
            padding: '6px 12px',
            borderRadius: 'var(--radius-pill)',
            border: '1px solid var(--hairline)',
            background: 'var(--surface)',
            fontSize: 13,
            fontWeight: 600,
          }}
        >
          <Dot color="var(--accent)" />
          {clientName}
          <span
            className="label"
            style={{
              fontSize: 9.5,
              background: 'var(--surface-alt)',
              border: '1px solid var(--hairline)',
              borderRadius: 'var(--radius-chip)',
              padding: '2px 6px',
              color: 'var(--text-muted)',
            }}
          >
            {pack}
          </span>
        </span>
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 8,
            padding: '6px 12px',
            borderRadius: 'var(--radius-pill)',
            border: '1px solid var(--hairline)',
            background: 'var(--surface)',
            fontSize: 13,
          }}
        >
          <Dot color="var(--accent)" live />
          <span className="mono" style={{ fontSize: 12 }}>
            LangGraph · live
          </span>
        </span>
      </div>
    </header>
  );
}
