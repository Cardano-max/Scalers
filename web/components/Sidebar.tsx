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
  collapsed = false,
  onToggleCollapsed,
}: {
  reviewCount: number;
  engineState: EngineState;
  onToggleEngine: () => void;
  operatorName?: string;
  /** Icon-rail mode (toggled by Ctrl/Cmd+B or the chevron). */
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
}) {
  const { screen, navigate } = useConsole();

  return (
    <aside
      data-collapsed={collapsed ? 'true' : 'false'}
      style={{
        width: collapsed ? 60 : 252,
        minWidth: collapsed ? 60 : 252,
        background: 'var(--surface)',
        borderRight: '1px solid var(--hairline)',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        transition: 'width 180ms cubic-bezier(0.22,1,0.36,1)',
        overflow: 'hidden',
      }}
    >
      {/* brand + collapse toggle */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: collapsed ? '18px 0' : '18px 16px',
          justifyContent: collapsed ? 'center' : 'flex-start',
        }}
      >
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
            flex: '0 0 auto',
          }}
        >
          S
        </div>
        {!collapsed && (
          <>
            <div style={{ lineHeight: 1.1 }}>
              <div style={{ fontWeight: 700, fontSize: 15 }}>Scalers</div>
              <div className="label" style={{ fontSize: 9.5 }}>
                Operator Console
              </div>
            </div>
            <span style={{ flex: 1 }} />
            <CollapseToggle collapsed={collapsed} onToggle={onToggleCollapsed} />
          </>
        )}
      </div>

      {collapsed && (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '0 0 8px' }}>
          <CollapseToggle collapsed={collapsed} onToggle={onToggleCollapsed} />
        </div>
      )}

      {!collapsed && (
        <div className="label" style={{ padding: '4px 18px 8px' }}>
          Workspace
        </div>
      )}

      {/* nav */}
      <nav style={{ padding: collapsed ? '0 8px' : '0 10px', display: 'flex', flexDirection: 'column', gap: 2 }}>
        {NAV_ITEMS.map((item) => {
          const active = screen === item.id;
          return (
            <button
              key={item.id}
              type="button"
              aria-current={active ? 'page' : undefined}
              title={collapsed ? item.label : undefined}
              onClick={() => navigate(item.id)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: collapsed ? 0 : 10,
                padding: collapsed ? '9px 0' : '9px 12px',
                justifyContent: collapsed ? 'center' : 'flex-start',
                borderRadius: 'var(--radius-button)',
                border: 'none',
                cursor: 'pointer',
                textAlign: 'left',
                font: 'inherit',
                fontSize: 14,
                fontWeight: active ? 600 : 500,
                color: active ? 'var(--accent-dark)' : 'var(--text-secondary)',
                background: active ? 'var(--nav-active-bg)' : 'transparent',
                position: 'relative',
              }}
            >
              <span style={{ color: active ? 'var(--accent)' : 'var(--text-muted)', display: 'flex' }}>
                <NavIcon id={item.id} />
              </span>
              {!collapsed && <span style={{ flex: 1 }}>{item.label}</span>}
              {item.id === 'review' && reviewCount > 0 ? (
                <span
                  aria-label={`${reviewCount} in review queue`}
                  style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: collapsed ? 9 : 11,
                    color: 'var(--amber-text)',
                    background: 'var(--amber-bg)',
                    border: '1px solid var(--amber-border)',
                    borderRadius: 'var(--radius-pill)',
                    padding: collapsed ? '0 4px' : '1px 8px',
                    position: collapsed ? 'absolute' : 'static',
                    top: collapsed ? 4 : undefined,
                    right: collapsed ? 6 : undefined,
                  }}
                >
                  {reviewCount}
                </span>
              ) : null}
              {item.id === 'feed' && !collapsed ? <Dot color="var(--danger-dot)" size={7} live /> : null}
            </button>
          );
        })}
      </nav>

      <div style={{ flex: 1 }} />

      {!collapsed && (
        <HarnessStatusCard
          engineState={engineState}
          onToggle={onToggleEngine}
          operatorName={operatorName}
        />
      )}
    </aside>
  );
}

/** The VS Code-style collapse chevron. Ctrl/Cmd+B does the same toggle. */
function CollapseToggle({ collapsed, onToggle }: { collapsed: boolean; onToggle?: () => void }) {
  if (!onToggle) return null;
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={collapsed ? 'Expand sidebar (Ctrl+B)' : 'Collapse sidebar (Ctrl+B)'}
      aria-pressed={collapsed}
      title={collapsed ? 'Expand sidebar (Ctrl+B)' : 'Collapse sidebar (Ctrl+B)'}
      style={{
        display: 'grid',
        placeItems: 'center',
        width: 26,
        height: 26,
        borderRadius: 7,
        border: '1px solid var(--hairline)',
        background: 'var(--surface-alt)',
        color: 'var(--text-muted)',
        cursor: 'pointer',
        flex: '0 0 auto',
      }}
    >
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <line x1="9" y1="4" x2="9" y2="20" />
        {collapsed ? <path d="M13 9l3 3-3 3" /> : <path d="M16 9l-3 3 3 3" />}
      </svg>
    </button>
  );
}
