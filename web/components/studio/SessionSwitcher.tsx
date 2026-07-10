'use client';

/**
 * SessionSwitcher — Claude-style conversation sessions for the studio.
 *
 * Lists the real sessions from GET /studio/sessions (title = the session's first
 * operator line, plus turn count and last activity), lets the operator switch to
 * any of them (its transcript hydrates from the server) or start a NEW empty
 * session with its own context. Voice and text share whichever session is active.
 */
import { useEffect, useState } from 'react';
import { useSharedStudio } from '@/lib/studio/StudioRunProvider';
import { clockTime } from '../console-bits';

type SessionRow = {
  sessionId: string;
  title: string;
  turns: number;
  lastAt: string | null;
};

function studioBase(aguiUrl: string): string {
  return aguiUrl.replace(/\/agui(\?.*)?$/, '');
}

/** Option label: title · turn count · when it was last active (date + time —
 *  entries without a timestamp simply omit it, never a fake one). */
function optionLabel(s: SessionRow): string {
  const when = s.lastAt ? ` · ${clockTime(s.lastAt)}` : '';
  return `${s.title.slice(0, 40)} · ${s.turns} turn${s.turns === 1 ? '' : 's'}${when}`;
}

export function SessionSwitcher() {
  const studio = useSharedStudio();
  const [sessions, setSessions] = useState<SessionRow[]>([]);

  useEffect(() => {
    if (!studio.aguiUrl) return;
    let alive = true;
    fetch(`${studioBase(studio.aguiUrl)}/sessions`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d) => alive && setSessions(d.sessions ?? []))
      .catch(() => alive && setSessions([]));
    return () => {
      alive = false;
    };
    // Re-list when the active session changes (a new session appears once it has turns).
  }, [studio.aguiUrl, studio.sessionId]);

  const active = studio.sessionId;
  const known = sessions.some((s) => s.sessionId === active);

  return (
    <div style={{ display: 'flex', gap: 6, alignItems: 'center', minWidth: 0 }}>
      {/* Tiny caption so this dropdown is never mistaken for the Studio picker. */}
      <label
        htmlFor="conversation-session-select"
        className="label"
        style={{ fontSize: 9 }}
      >
        Conversation
      </label>
      <select
        id="conversation-session-select"
        aria-label="Conversation session"
        value={active}
        onChange={(e) => studio.switchSession(e.target.value)}
        style={{
          font: 'inherit',
          fontSize: 11.5,
          padding: '5px 8px',
          maxWidth: 280,
          border: '1px solid var(--hairline)',
          borderRadius: 'var(--radius-button)',
          background: 'var(--surface)',
          color: 'var(--text-secondary)',
        }}
      >
        {!known && <option value={active}>current: {active.slice(0, 22)}</option>}
        {sessions.map((s) => (
          <option key={s.sessionId} value={s.sessionId}>
            {optionLabel(s)}
          </option>
        ))}
      </select>
      <button
        type="button"
        onClick={() => studio.newSession()}
        title="Start a new conversation session with fresh context"
        style={{
          font: 'inherit',
          fontSize: 11.5,
          fontWeight: 600,
          padding: '5px 10px',
          border: '1px solid var(--hairline)',
          borderRadius: 'var(--radius-button)',
          background: 'var(--surface)',
          color: 'var(--text-secondary)',
          cursor: 'pointer',
          whiteSpace: 'nowrap',
        }}
      >
        + New session
      </button>
    </div>
  );
}
