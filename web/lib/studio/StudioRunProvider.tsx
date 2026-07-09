'use client';

/**
 * StudioRunProvider — hoists ONE live `useStudioAgui` instance into context so the
 * two headline modes (Voice + Agency) share the SAME run. Switching between them
 * does NOT unmount/rebuild the studio, so the live per-agent reasoning stream (and
 * the in-flight run it is polling) persists across the tab switch.
 *
 * HONESTY: the hook owns all real wiring (probe → connected/preview, POST /studio/run,
 * GET /studio/run/{id} polling, the approval gate). With no studio endpoint configured
 * the hook reports `connected: false` and every action is a no-op — the screens then
 * render their honest not-connected state, never a fabricated run.
 */
import { createContext, useContext, type ReactNode } from 'react';
import { useStudioAgui, type UseStudioAgui } from './useStudioAgui';

export interface StudioRunContext extends UseStudioAgui {
  /** The resolved AG-UI endpoint (or '' when unconfigured) — voice needs it directly. */
  aguiUrl: string;
  /** The studio session id shared by voice + the orchestration run. */
  sessionId: string;
  /** True only when an AG-UI endpoint is actually configured (vs unreachable). */
  configured: boolean;
}

const Ctx = createContext<StudioRunContext | null>(null);

function resolveUrls() {
  const aguiUrl =
    (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_STUDIO_AGUI_URL) || '';
  const graphqlUrl =
    (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_STUDIO_GRAPHQL_URL) || '/graphql';
  return { aguiUrl, graphqlUrl };
}

export function StudioRunProvider({
  children,
  sessionId = 'studio-live-session',
}: {
  children: ReactNode;
  sessionId?: string;
}) {
  const { aguiUrl, graphqlUrl } = resolveUrls();
  const studio = useStudioAgui(aguiUrl, graphqlUrl, sessionId);
  const value: StudioRunContext = {
    ...studio,
    aguiUrl,
    sessionId,
    configured: aguiUrl.length > 0,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** Read the shared studio run. Throws if used outside the provider (a wiring bug). */
export function useSharedStudio(): StudioRunContext {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useSharedStudio must be used within a <StudioRunProvider>');
  return ctx;
}

/** Non-throwing read for screens that must also render OUTSIDE the provider
 *  (unit tests render them bare). Returns null when no provider is mounted. */
export function useSharedStudioOptional(): StudioRunContext | null {
  return useContext(Ctx);
}
