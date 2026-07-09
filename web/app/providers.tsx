'use client';

/**
 * Client provider stack for the console:
 *   DataProvider    — the active kkg.4 adapter (mock | live) + tenant
 *   ConsoleProvider — active-screen nav + transient edit state
 *
 * The old CopilotKit wrapper was removed (QA 5c): the Command screen it served
 * is gone (Voice/Agency speak AG-UI directly via lib/studio/agui.ts) and the
 * inert provider still fired a dead POST /api/copilotkit 404 on every load.
 */
import type { ReactNode } from 'react';
import { DataProvider } from '@/lib/data/DataProvider';
import { ConsoleProvider } from '@/state/console-store';

export function Providers({ children }: { children: ReactNode }) {
  return (
    <DataProvider>
      {/* Voice-first: the talk-to-your-agency hero is the default landing surface. */}
      <ConsoleProvider initialScreen="voice">{children}</ConsoleProvider>
    </DataProvider>
  );
}
