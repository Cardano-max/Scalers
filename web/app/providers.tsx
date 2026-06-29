'use client';

/**
 * Client provider stack for the console:
 *   CopilotKit (AG-UI)  — agent chat + generative UI (Command screen, bead 45v.9)
 *   DataProvider        — the active kkg.4 adapter (mock | live) + tenant
 *   ConsoleProvider     — active-screen nav + transient edit state
 *
 * CopilotKit's runtime URL is env-driven; with no runtime configured the
 * provider is inert (the Command screen wires the real endpoint in its bead).
 */
import type { ReactNode } from 'react';
import { CopilotKit } from '@copilotkit/react-core';
import { DataProvider } from '@/lib/data/DataProvider';
import { ConsoleProvider } from '@/state/console-store';

export function Providers({ children }: { children: ReactNode }) {
  const runtimeUrl = process.env.NEXT_PUBLIC_COPILOT_RUNTIME_URL ?? '/api/copilotkit';
  return (
    <CopilotKit runtimeUrl={runtimeUrl}>
      <DataProvider>
        <ConsoleProvider>{children}</ConsoleProvider>
      </DataProvider>
    </CopilotKit>
  );
}
