'use client';

/**
 * Fixed 252px sidebar: brand, nav (with the amber Review-queue badge + the Live
 * feed live-dot), and the harness status card with the master Pause/Resume
 * control + operator chip. Nav switches the single active `screen`; the active
 * item gets the teal tint.
 */
import { NAV_ITEMS, useConsole } from '@/state/console-store';
import { NavIcon, Dot } from './icons';
import { HarnessStatusCard } from './HarnessStatusCard';
import type { EngineState } from '@/lib/data/models';

export function Sidebar({
  reviewCount,
  engineState,
  onToggleEngine,
  operatorName = 'Jordan Tran',
}: {
  reviewCount: number;
  engineState: EngineState;
  onToggleEngine: () => void;
  operatorName?: string;
}) {
  const { screen, navigate } = useConsole();

  return (
    <aside
      style={{
        width: 252,
        minWidth: 252,
        background: 'var(--surface)',
        borderRight: '1px solid var(--hairline)',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
      }}
    >
      {/* brand */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '18px 16px' }}>
        <div
          style={{
            width: 30,
            height: 30,
            borderRadius: 8,
            background: 'var(--accent)',
            color: '#fff',
            display: 'grid',
            placeItems: 'center',
            fontWeight: 700,
            fontSize: 16,
          }}
        >
          S
        </div>
        <div style={{ lineHeight: 1.1 }}>
          <div style={{ fontWeight: 700, fontSize: 15 }}>Scalers</div>
          <div className="label" style={{ fontSize: 9.5 }}>
            Operator Console
          </div>
        </div>
      </div>

      <div className="label" style={{ padding: '4px 18px 8px' }}>
        Workspace
      </div>

      {/* nav */}
      <nav style={{ padding: '0 10px', display: 'flex', flexDirection: 'column', gap: 2 }}>
        {NAV_ITEMS.map((item) => {
          const active = screen === item.id;
          return (
            <button
              key={item.id}
              type="button"
              aria-current={active ? 'page' : undefined}
              onClick={() => navigate(item.id)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '9px 12px',
                borderRadius: 'var(--radius-button)',
                border: 'none',
                cursor: 'pointer',
                textAlign: 'left',
                font: 'inherit',
                fontSize: 14,
                fontWeight: active ? 600 : 500,
                color: active ? 'var(--accent-dark)' : 'var(--text-secondary)',
                background: active ? 'var(--nav-active-bg)' : 'transparent',
              }}
            >
              <span style={{ color: active ? 'var(--accent)' : 'var(--text-muted)', display: 'flex' }}>
                <NavIcon id={item.id} />
              </span>
              <span style={{ flex: 1 }}>{item.label}</span>
              {item.id === 'review' && reviewCount > 0 ? (
                <span
                  aria-label={`${reviewCount} in review queue`}
                  style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: 11,
                    color: 'var(--amber-text)',
                    background: 'var(--amber-bg)',
                    border: '1px solid var(--amber-border)',
                    borderRadius: 'var(--radius-pill)',
                    padding: '1px 8px',
                  }}
                >
                  {reviewCount}
                </span>
              ) : null}
              {item.id === 'feed' ? <Dot color="var(--danger-dot)" size={7} live /> : null}
            </button>
          );
        })}
      </nav>

      <div style={{ flex: 1 }} />

      <HarnessStatusCard
        engineState={engineState}
        onToggle={onToggleEngine}
        operatorName={operatorName}
      />
    </aside>
  );
}
