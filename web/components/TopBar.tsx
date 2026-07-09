'use client';

/**
 * 64px top bar: page title + subtitle (left); client pill + "LangGraph · live"
 * pill (right). The title/subtitle are derived from the active screen.
 */
import { useConsole, type ScreenId } from '@/state/console-store';
import { useData } from '@/lib/data/DataProvider';
import { SELECTABLE_TENANTS } from '@/lib/data';
import { Dot } from './icons';

const TITLES: Record<ScreenId, { title: string; subtitle: string }> = {
  voice: { title: 'Voice', subtitle: 'Talk to your strategist — it interviews you, then spins up the team' },
  agency: { title: 'Agency at work', subtitle: 'Watch the team orchestrate, research, draft, re-verify, and evaluate — live' },
  artists: { title: 'Artists', subtitle: 'Roster, artwork, past campaigns, and per-artist memory' },
  overview: { title: 'Overview', subtitle: 'Autonomy, deliverability, and what needs you' },
  review: { title: 'Review queue', subtitle: 'Actions the engine escalated for your sign-off' },
  activity: { title: 'Activity', subtitle: 'What the agents executed — and why' },
  feed: { title: 'Live feed', subtitle: 'Realtime decision stream' },
  runs: { title: 'Runs', subtitle: 'LangGraph / Temporal workflow history' },
  memory: { title: 'Campaign memory', subtitle: 'Real past campaigns — metrics, copy, and source screenshots' },
  // drill-only; reached via navigate('step_detail', actionId), not nav bar
  step_detail: { title: 'Step detail', subtitle: 'Full trace and jury derivation for this action' },
};

export function TopBar({
  clientName,
  pack,
}: {
  clientName: string;
  pack: string;
}) {
  const { screen } = useConsole();
  const { tenantId, setTenantId } = useData();
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
        {/* ju1.5 tenant switcher: skindesign is the default; ladies8391 stays
            selectable as the dev fixture. The choice persists (localStorage). */}
        <select
          aria-label="Tenant"
          value={SELECTABLE_TENANTS.some((t) => t.id === tenantId) ? tenantId : ''}
          onChange={(e) => e.target.value && setTenantId(e.target.value)}
          style={{
            padding: '6px 10px',
            borderRadius: 'var(--radius-pill)',
            border: '1px solid var(--hairline)',
            background: 'var(--surface)',
            fontSize: 12.5,
            fontWeight: 600,
            color: 'var(--text-primary, inherit)',
          }}
        >
          {!SELECTABLE_TENANTS.some((t) => t.id === tenantId) && (
            <option value="">{tenantId}</option>
          )}
          {SELECTABLE_TENANTS.map((t) => (
            <option key={t.id} value={t.id}>
              {t.label}
            </option>
          ))}
        </select>
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
