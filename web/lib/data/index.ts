/**
 * Data layer entrypoint. Selects the adapter from NEXT_PUBLIC_DATA_SOURCE:
 *   live (default) -> the urql + SSE LiveAdapter bound to the REAL engine
 *                     GraphQL + SSE (obsapi at :8010). The non-studio tabs
 *                     (Overview / Review queue / Activity / Live feed / Runs)
 *                     render REAL runs + actions, not seeded fixtures.
 *   mock           -> the clearly-labeled MockAdapter (clean offline fallback,
 *                     no backend needed). Opt in with NEXT_PUBLIC_DATA_SOURCE=mock.
 * Flipping the env var is the ONLY change needed to go from live to mock.
 *
 * The default endpoints are SAME-ORIGIN (`/graphql`, `/sse/stream`): next.config
 * rewrites proxy them to the engine origin (STUDIO_BACKEND_ORIGIN, default
 * http://127.0.0.1:8010) so the browser never needs CORS and works on any port.
 *
 * NOTE: this shared adapter drives ONLY the non-studio tabs. The Campaign Studio
 * (Command tab) uses its own `createStudioAdapter()` and is unaffected by this
 * source flip.
 */
import type { DataAdapter } from './adapter';
import { MockAdapter, MOCK_TENANT_ID } from './mock-adapter';
import { LiveAdapter } from './live-adapter';

export type DataSource = 'mock' | 'live';

/**
 * The default live tenant. Per operator order (ju1.5) the console now lands on
 * the REAL client tenant `skindesign` (TEST MODE, server-gated sends);
 * `ladies8391` remains selectable as the dev fixture via the tenant switcher.
 */
export const LIVE_TENANT_ID = 'skindesign';

/** Tenants the TopBar switcher offers. Order = default first. */
export const SELECTABLE_TENANTS: { id: string; label: string }[] = [
  { id: 'skindesign', label: 'Skin Design Tattoo' },
  { id: 'ladies8391', label: 'Ladies First (dev fixture)' },
];

export interface DataLayerEnv {
  source?: string;
  graphqlUrl?: string;
  sseUrl?: string;
  tenantId?: string;
}

/**
 * Resolve the active source. The build DEFAULTS to `live` for these tabs so a
 * normal `next build` shows real engine data; `mock` is an explicit opt-out that
 * keeps a clean, backend-free fallback for offline/demo work.
 */
export function resolveDataSource(value?: string): DataSource {
  return value === 'mock' ? 'mock' : 'live';
}

/** Build the active adapter from env (with sane same-origin defaults). */
export function createAdapter(env: DataLayerEnv = readEnv()): DataAdapter {
  const source = resolveDataSource(env.source);
  if (source === 'live') {
    return new LiveAdapter({
      graphqlUrl: env.graphqlUrl ?? '/graphql',
      sseUrl: env.sseUrl ?? '/sse/stream',
    });
  }
  return new MockAdapter();
}

/** Read the NEXT_PUBLIC_* env (inlined by Next at build time). */
export function readEnv(): DataLayerEnv {
  return {
    source: process.env.NEXT_PUBLIC_DATA_SOURCE,
    graphqlUrl: process.env.NEXT_PUBLIC_GRAPHQL_URL,
    sseUrl: process.env.NEXT_PUBLIC_SSE_URL,
    tenantId: process.env.NEXT_PUBLIC_TENANT_ID,
  };
}

/**
 * The single active tenant carried in every query/subscription. On the live
 * source the default is the real demo tenant (ladies8391); on mock it is the
 * mock tenant. An explicit NEXT_PUBLIC_TENANT_ID always wins.
 */
export function activeTenantId(env: DataLayerEnv = readEnv()): string {
  if (env.tenantId) return env.tenantId;
  return resolveDataSource(env.source) === 'live' ? LIVE_TENANT_ID : MOCK_TENANT_ID;
}

export type { DataAdapter } from './adapter';
export * from './models';
export * from './sse';
