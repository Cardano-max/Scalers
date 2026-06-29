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
  | 'overview'
  | 'review'
  | 'activity'
  | 'feed'
  | 'runs'
  | 'command';

export interface NavItemDef {
  id: ScreenId;
  label: string;
}

/** The locked nav order (Activity included — handoff "NEW" screen / bead 45v.4). */
export const NAV_ITEMS: NavItemDef[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'review', label: 'Review queue' },
  { id: 'activity', label: 'Activity' },
  { id: 'feed', label: 'Live feed' },
  { id: 'runs', label: 'Runs' },
  { id: 'command', label: 'Command' },
];

interface ConsoleState {
  screen: ScreenId;
  /** Whether an inline edit (e.g. Review-queue draft) is open. Reset on nav. */
  editing: boolean;
  /** The in-progress edit buffer. Reset on nav. */
  draftText: string;
}

type Action =
  | { type: 'navigate'; screen: ScreenId }
  | { type: 'startEditing'; draftText: string }
  | { type: 'setDraft'; draftText: string }
  | { type: 'cancelEditing' };

function reducer(state: ConsoleState, action: Action): ConsoleState {
  switch (action.type) {
    case 'navigate':
      if (action.screen === state.screen) return state;
      // Reset editing state on every screen switch (handoff behavior).
      return { screen: action.screen, editing: false, draftText: '' };
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
  navigate: (screen: ScreenId) => void;
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
    editing: false,
    draftText: '',
  });

  const store = useMemo<ConsoleStore>(
    () => ({
      ...state,
      navigate: (screen) => dispatch({ type: 'navigate', screen }),
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

// Exported for unit testing the reset-on-nav invariant directly.
export const __reducer = reducer;
