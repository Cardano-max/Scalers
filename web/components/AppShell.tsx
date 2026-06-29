'use client';

/**
 * App shell — the full-viewport flex row every screen plugs into: the 252px
 * Sidebar + a flex column (64px TopBar over the screen area). Each screen is
 * absolutely positioned to fill the area; only the active one renders (single
 * `screen` state). The shell owns the tenant read (engine state + Review-queue
 * badge count) and the master Pause/Resume.
 */
import { useState, useCallback } from 'react';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { useConsole } from '@/state/console-store';
import { useData } from '@/lib/data/DataProvider';
import { useAsync } from '@/lib/useAsync';
import { SCREENS } from './screens';
import type { EngineState } from '@/lib/data/models';

export function AppShell() {
  const { adapter, tenantId } = useData();
  const { screen } = useConsole();

  // Optimistic engine-state cell so Pause/Resume feels instant; reconciled by reload.
  const [engineOverride, setEngineOverride] = useState<EngineState | null>(null);

  const tenant = useAsync(() => adapter.getTenant(tenantId), [tenantId]);
  const queue = useAsync(() => adapter.getReviewQueue(tenantId), [tenantId]);

  const engineState: EngineState =
    engineOverride ?? tenant.data?.engineState ?? 'RUNNING';
  const reviewCount = queue.data?.length ?? 0;

  const onToggleEngine = useCallback(async () => {
    const next: EngineState = engineState === 'RUNNING' ? 'PAUSED' : 'RUNNING';
    setEngineOverride(next);
    try {
      await adapter.setEngineState(tenantId, next === 'PAUSED');
    } catch {
      setEngineOverride(null); // revert optimism on failure
      tenant.reload();
    }
  }, [adapter, tenantId, engineState, tenant]);

  const ActiveScreen = SCREENS[screen];

  return (
    <div style={{ display: 'flex', height: '100vh', background: 'var(--app-bg)' }}>
      <Sidebar
        reviewCount={reviewCount}
        engineState={engineState}
        onToggleEngine={onToggleEngine}
        operatorName={tenant.data?.name ? 'Jordan Tran' : 'Jordan Tran'}
      />
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <TopBar
          clientName={tenant.data?.name ?? 'Loading…'}
          pack={tenant.data?.pack ?? ''}
        />
        {/* screen area: position:relative; each screen inset:0, active one rendered */}
        <section style={{ position: 'relative', flex: 1, overflow: 'auto', background: 'var(--canvas)' }}>
          <div className="enter" style={{ position: 'absolute', inset: 0, overflow: 'auto' }}>
            <ActiveScreen />
          </div>
        </section>
      </main>
    </div>
  );
}
