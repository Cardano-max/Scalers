'use client';

/**
 * React context that exposes the active DataAdapter + the active tenant id to
 * every screen. Components call `useData()` and never know whether they're on
 * the mock or the live kkg.4 backend.
 *
 * ju1.5: the tenant is now SWITCHABLE at runtime (operator order: `skindesign`
 * is the default; `ladies8391` stays selectable as the dev fixture). The choice
 * persists in localStorage; an explicit `tenantId` prop (tests) or
 * NEXT_PUBLIC_TENANT_ID still pins it.
 */
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { createAdapter, activeTenantId, type DataAdapter } from './index';

const TENANT_STORAGE_KEY = 'console.tenantId';

interface DataContextValue {
  adapter: DataAdapter;
  tenantId: string;
  /** Switch the active tenant (persisted). No-op when the tenant was pinned via prop. */
  setTenantId: (id: string) => void;
}

const DataContext = createContext<DataContextValue | null>(null);

function storedTenant(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.localStorage.getItem(TENANT_STORAGE_KEY);
  } catch {
    return null;
  }
}

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
  const pinned = tenantId != null;
  const [tenant, setTenant] = useState<string>(
    () => tenantId ?? storedTenant() ?? activeTenantId(),
  );

  useEffect(() => {
    if (pinned || typeof window === 'undefined') return;
    try {
      window.localStorage.setItem(TENANT_STORAGE_KEY, tenant);
    } catch {
      /* private mode etc. — the in-memory choice still works */
    }
  }, [pinned, tenant]);

  const value = useMemo<DataContextValue>(
    () => ({
      adapter: adapter ?? createAdapter(),
      tenantId: pinned ? (tenantId as string) : tenant,
      setTenantId: (id: string) => {
        if (!pinned) setTenant(id);
      },
    }),
    [adapter, pinned, tenantId, tenant],
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
