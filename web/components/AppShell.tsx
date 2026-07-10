'use client';

/**
 * App shell — the full-viewport flex row every screen plugs into: the 252px
 * Sidebar + a flex column (64px TopBar over the screen area). Each screen is
 * absolutely positioned to fill the area; only the active one renders (single
 * `screen` state). The shell owns the tenant read (engine state + Review-queue
 * badge count) and the master Pause/Resume.
 */
import { useState, useCallback, useEffect, useRef } from 'react';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { TestModeBanner } from './TestModeBanner';
import { StatusStrip } from './StatusStrip';
import { useConsole } from '@/state/console-store';
import { useData } from '@/lib/data/DataProvider';
import { useAsync } from '@/lib/useAsync';
import { SCREENS } from './screens';
import { StudioRunProvider } from '@/lib/studio/StudioRunProvider';
import type { EngineState } from '@/lib/data/models';

const SIDEBAR_COLLAPSE_KEY = 'scalers.sidebar.collapsed';

export function AppShell() {
  const { adapter, tenantId } = useData();
  const { screen, navigate } = useConsole();

  // ── URL-hash routing ──────────────────────────────────────────────────────
  // Every screen switch updates the URL hash (history.pushState) and browser
  // back/forward + pasted deep links (#review, #runs, …) navigate the console —
  // real, shareable navigation, still on the existing screen registry.
  useEffect(() => {
    const hash =
      typeof window !== 'undefined' ? window.location.hash.replace('#', '') : '';
    if (hash && hash in SCREENS) {
      navigate(hash as keyof typeof SCREENS);
    }
    // run once on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reflect the active screen into the hash. IMPORTANT: never write while an
  // unconsumed deep-link hash exists (e.g. arriving on /#review while state
  // still says the default screen) — writing then would clobber the deep link
  // before the mount navigation lands (StrictMode double-effects made this a
  // real race). We only start writing once state and hash have met.
  const hashSynced = useRef(false);
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const current = window.location.hash.replace('#', '');
    try {
      if (!hashSynced.current) {
        // Still converging: a valid, different hash is a deep link the mount
        // effect is about to consume — leave it alone.
        if (current && current in SCREENS && current !== screen) return;
        hashSynced.current = true;
        if (current !== screen) {
          // Initial mount: set the default hash without growing history.
          window.history.replaceState(null, '', `#${screen}`);
        }
        return;
      }
      if (current === screen) return;
      window.history.pushState(null, '', `#${screen}`);
    } catch {
      /* history unavailable (very old browsers / odd embeds) — nav still works */
    }
  }, [screen]);

  // Back/forward + manual hash edits navigate the console.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const onHash = () => {
      const hash = window.location.hash.replace('#', '');
      if (hash && hash in SCREENS) navigate(hash as keyof typeof SCREENS);
    };
    window.addEventListener('popstate', onHash);
    window.addEventListener('hashchange', onHash);
    return () => {
      window.removeEventListener('popstate', onHash);
      window.removeEventListener('hashchange', onHash);
    };
  }, [navigate]);

  // Collapsible left sidebar (VS Code / Cursor parity), toggled by Ctrl/Cmd+B and a
  // visible chevron, persisted to localStorage so it survives reloads.
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      setSidebarCollapsed(window.localStorage.getItem(SIDEBAR_COLLAPSE_KEY) === '1');
    } catch {
      /* localStorage unavailable — default expanded */
    }
  }, []);
  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(SIDEBAR_COLLAPSE_KEY, next ? '1' : '0');
      } catch {
        /* ignore persistence failure */
      }
      return next;
    });
  }, []);
  // Real Ctrl+B / Cmd+B keyboard shortcut, exactly like VS Code / Cursor.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && (e.key === 'b' || e.key === 'B')) {
        e.preventDefault();
        toggleSidebar();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [toggleSidebar]);

  // Optimistic engine-state cell so Pause/Resume feels instant; reconciled by reload.
  const [engineOverride, setEngineOverride] = useState<EngineState | null>(null);

  const tenant = useAsync(() => adapter.getTenant(tenantId), [tenantId]);
  // The REST tenant record (GET /tenants/{id}) carries the human name ("Skin
  // Design Tattoo") even when the GraphQL tenant query fails — the top-bar chip
  // prefers it over a raw tenant id and must NEVER be stuck on "Loading…" (QA 5b).
  const tenantMeta = useAsync(() => adapter.getTenantMeta(tenantId), [tenantId]);
  const queue = useAsync(() => adapter.getReviewQueue(tenantId), [tenantId]);

  const clientName =
    tenant.data?.name ??
    tenantMeta.data?.name ??
    (tenant.loading || tenantMeta.loading ? 'Loading…' : tenantId);

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
        collapsed={sidebarCollapsed}
        onToggleCollapsed={toggleSidebar}
      />
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <TopBar
          clientName={clientName}
          pack={tenant.data?.pack ?? ''}
        />
        {/* ju1.5: server-driven TEST-MODE banner — renders only when the tenants
            API reports testMode; the ladies8391 dev fixture shows nothing. */}
        <TestModeBanner />
        {/* What's-happening strip — visible on every screen; reuses the shared
            fleet poll + the review count this shell already loads. */}
        <StatusStrip reviewCount={reviewCount} />
        {/* screen area: position:relative; each screen inset:0, active one rendered.
            The StudioRunProvider lives HERE (above the single active-screen mount) so
            the Voice + Agency tabs share ONE live run — switching between them does not
            rebuild the run or lose the per-agent reasoning stream. */}
        <StudioRunProvider>
          <section style={{ position: 'relative', flex: 1, overflow: 'auto', background: 'var(--canvas)' }}>
            {/* keyed by screen id so the fade/slide-up transition re-runs on
                every switch (prefers-reduced-motion turns it off). */}
            <div key={screen} className="screen-enter" style={{ position: 'absolute', inset: 0, overflow: 'auto' }}>
              <ActiveScreen />
            </div>
          </section>
        </StudioRunProvider>
      </main>
    </div>
  );
}
