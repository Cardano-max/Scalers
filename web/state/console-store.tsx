'use client';

/**
 * Console navigation + transient-edit state. A single `screen` value drives
 * which screen is active (each screen is absolutely positioned to fill the
 * area; only the active one renders). Per the handoff: switching screens RESETS
 * editing state — so an in-progress inline edit on the Review queue is discarded
 * when the operator navigates away, never silently carried to another screen.
 */
import {
  createContext,
  useContext,
  useReducer,
  useMemo,
  type ReactNode,
} from 'react';

export type ScreenId =
  | 'voice'
  | 'agency'
  | 'overview'
  | 'review'
  | 'activity'
  | 'feed'
  | 'runs'
  | 'command'
  | 'step_detail';

export interface NavItemDef {
  id: ScreenId;
  label: string;
}

/**
 * Nav order. Voice + Agency are the two headline modes (the flashy front door):
 * Voice first (talk to the agency), Agency second (watch it work). Command is the
 * hands-on plan+chat workbench; the real-data tabs follow, unchanged.
 */
export const NAV_ITEMS: NavItemDef[] = [
  { id: 'voice', label: 'Voice' },
  { id: 'agency', label: 'Agency' },
  { id: 'command', label: 'Command' },
  { id: 'overview', label: 'Overview' },
  { id: 'review', label: 'Review queue' },
  { id: 'activity', label: 'Activity' },
  { id: 'feed', label: 'Live feed' },
  { id: 'runs', label: 'Runs' },
];

interface ConsoleState {
  screen: ScreenId;
  /** Optional context ID (action/run/feed-event) to auto-select when navigating. */
  contextId: string | null;
  /** Whether an inline edit (e.g. Review-queue draft) is open. Reset on nav. */
  editing: boolean;
  /** The in-progress edit buffer. Reset on nav. */
  draftText: string;
}

type Action =
  | { type: 'navigate'; screen: ScreenId; contextId?: string | null }
  | { type: 'setContext'; contextId: string | null }
  | { type: 'startEditing'; draftText: string }
  | { type: 'setDraft'; draftText: string }
  | { type: 'cancelEditing' };

function reducer(state: ConsoleState, action: Action): ConsoleState {
  switch (action.type) {
    case 'navigate':
      if (action.screen === state.screen) {
        // Same screen: a bare nav (no target) stays a no-op so an in-progress
        // edit buffer survives (handoff rule). But an intra-screen DEEP-LINK
        // (a chip/back-link that targets a specific row on the screen you are
        // already on) MUST update contextId — otherwise clicking "Open run" for
        // run B while already on Runs would do nothing. The editing buffer is
        // left intact: selecting a different row is not a screen switch.
        if (action.contextId == null) return state;
        return { ...state, contextId: action.contextId };
      }
      // Reset editing state on every screen switch (handoff behavior).
      return { screen: action.screen, contextId: action.contextId ?? null, editing: false, draftText: '' };
    case 'setContext':
      return { ...state, contextId: action.contextId };
    case 'startEditing':
      return { ...state, editing: true, draftText: action.draftText };
    case 'setDraft':
      return { ...state, draftText: action.draftText };
    case 'cancelEditing':
      return { ...state, editing: false, draftText: '' };
    default:
      return state;
  }
}

interface ConsoleStore extends ConsoleState {
  navigate: (screen: ScreenId, contextId?: string | null) => void;
  setContext: (contextId: string | null) => void;
  startEditing: (draftText: string) => void;
  setDraft: (draftText: string) => void;
  cancelEditing: () => void;
}

const ConsoleContext = createContext<ConsoleStore | null>(null);

export function ConsoleProvider({
  children,
  initialScreen = 'overview',
}: {
  children: ReactNode;
  initialScreen?: ScreenId;
}) {
  const [state, dispatch] = useReducer(reducer, {
    screen: initialScreen,
    contextId: null,
    editing: false,
    draftText: '',
  });

  const store = useMemo<ConsoleStore>(
    () => ({
      ...state,
      navigate: (screen, contextId) => dispatch({ type: 'navigate', screen, contextId }),
      setContext: (contextId) => dispatch({ type: 'setContext', contextId }),
      startEditing: (draftText) => dispatch({ type: 'startEditing', draftText }),
      setDraft: (draftText) => dispatch({ type: 'setDraft', draftText }),
      cancelEditing: () => dispatch({ type: 'cancelEditing' }),
    }),
    [state],
  );

  return (
    <ConsoleContext.Provider value={store}>{children}</ConsoleContext.Provider>
  );
}

export function useConsole(): ConsoleStore {
  const ctx = useContext(ConsoleContext);
  if (!ctx) throw new Error('useConsole must be used within a <ConsoleProvider>');
  return ctx;
}

/** Non-throwing read of the console store — returns null when used outside a
 *  <ConsoleProvider>. For optional reads (e.g. a deep-link contextId) on screens
 *  that must also render in isolation (unit tests render them bare). */
export function useConsoleOptional(): ConsoleStore | null {
  return useContext(ConsoleContext);
}

// Exported for unit testing the reset-on-nav invariant directly.
export const __reducer = reducer;
