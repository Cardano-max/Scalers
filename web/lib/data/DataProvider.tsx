'use client';

/**
 * React context that exposes the active DataAdapter + the active tenant id to
 * every screen. Components call `useData()` and never know whether they're on
 * the mock or the live kkg.4 backend.
 */
import { createContext, useContext, useMemo, type ReactNode } from 'react';
import { createAdapter, activeTenantId, type DataAdapter } from './index';

interface DataContextValue {
  adapter: DataAdapter;
  tenantId: string;
}

const DataContext = createContext<DataContextValue | null>(null);

export function DataProvider({
  children,
  adapter,
  tenantId,
}: {
  children: ReactNode;
  /** Override the adapter (tests inject a fake; default resolves from env). */
  adapter?: DataAdapter;
  tenantId?: string;
}) {
  const value = useMemo<DataContextValue>(
    () => ({
      adapter: adapter ?? createAdapter(),
      tenantId: tenantId ?? activeTenantId(),
    }),
    [adapter, tenantId],
  );
  return <DataContext.Provider value={value}>{children}</DataContext.Provider>;
}

export function useData(): DataContextValue {
  const ctx = useContext(DataContext);
  if (!ctx) {
    throw new Error('useData must be used within a <DataProvider>');
  }
  return ctx;
}
