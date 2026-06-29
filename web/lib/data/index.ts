/**
 * Data layer entrypoint. Selects the adapter from NEXT_PUBLIC_DATA_SOURCE:
 *   mock (default) -> the clearly-labeled MockAdapter (no backend needed)
 *   live           -> the urql + SSE LiveAdapter bound to the kkg.4 gateway
 * Flipping the env var is the ONLY change needed to go from mock to live.
 */
import type { DataAdapter } from './adapter';
import { MockAdapter, MOCK_TENANT_ID } from './mock-adapter';
import { LiveAdapter } from './live-adapter';

export type DataSource = 'mock' | 'live';

export interface DataLayerEnv {
  source?: string;
  graphqlUrl?: string;
  sseUrl?: string;
  tenantId?: string;
}

export function resolveDataSource(value?: string): DataSource {
  return value === 'live' ? 'live' : 'mock';
}

/** Build the active adapter from env (with sane local defaults). */
export function createAdapter(env: DataLayerEnv = readEnv()): DataAdapter {
  const source = resolveDataSource(env.source);
  if (source === 'live') {
    return new LiveAdapter({
      graphqlUrl: env.graphqlUrl ?? 'http://localhost:4000/graphql',
      sseUrl: env.sseUrl ?? 'http://localhost:4000/sse/stream',
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

/** The single active tenant carried in every query/subscription. */
export function activeTenantId(env: DataLayerEnv = readEnv()): string {
  return env.tenantId ?? MOCK_TENANT_ID;
}

export type { DataAdapter } from './adapter';
export * from './models';
export * from './sse';
