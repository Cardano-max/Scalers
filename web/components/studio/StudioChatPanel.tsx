'use client';

/**
 * StudioChatPanel — the conversation surface of the Campaign Studio.
 *
 * Renders a scrollable list of operator + agent-role turns (role label, text,
 * timestamp) and an input box + send. Built for STREAMING: turns with
 * `streaming: true` render a live caret and grow as deltas arrive (the parent
 * owns the turn array and appends/mutates it).
 *
 * HONESTY: this is presentational. It renders exactly the turns it is given and
 * nothing more — it never invents an agent reply. When `streamStatus` is
 * 'preview' it shows a persistent "not connected to live agents" note so the
 * operator is never misled into thinking a message was sent to a real agent.
 */
import { useEffect, useRef, useState } from 'react';
import {
  STUDIO_ROLE_COLOR,
  STUDIO_ROLE_LABEL,
  type ChatTurn,
  type StudioStreamStatus,
} from '@/lib/data/studio-adapter';

interface StudioChatPanelProps {
  turns: ChatTurn[];
  onSend: (text: string) => void;
  /** Stream lifecycle; 'preview' drives the honest not-connected note. */
  streamStatus: StudioStreamStatus;
  busy?: boolean;
}

function formatTime(at: string): string {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function StudioChatPanel({
  turns,
  onSend,
  streamStatus,
  busy = false,
}: StudioChatPanelProps) {
  const [draft, setDraft] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [turns]);

  const isPreview = streamStatus === 'preview';

  const submit = () => {
    const text = draft.trim();
    if (!text || busy) return;
    onSend(text);
    setDraft('');
  };

  return (
    <section
      aria-label="Campaign Studio conversation"
      style={{
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        flex: 1,
        background: '#fff',
        border: '1px solid var(--hairline)',
        borderRadius: 12,
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <header
        style={{
          padding: '12px 16px',
          borderBottom: '1px solid var(--hairline)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: '#1A1A17' }}>
          Conversation
        </h2>
        <span style={{ fontSize: 11, color: '#A8A299' }}>operator + agent team</span>
      </header>

      {/* Message list */}
      <div
        ref={scrollRef}
        role="log"
        aria-live="polite"
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          padding: '16px',
          display: 'flex',
          flexDirection: 'column',
          gap: 14,
        }}
      >
        {turns.length === 0 ? (
          <div
            style={{
              margin: 'auto',
              maxWidth: 360,
              textAlign: 'center',
              color: '#8C877D',
              fontSize: 13,
              lineHeight: 1.5,
            }}
          >
            <div style={{ fontSize: 15, fontWeight: 600, color: '#6B6461', marginBottom: 6 }}>
              No messages yet
            </div>
            Start a campaign brief below. The multi-agent team (Researcher,
            Strategist, Copywriter, Critic) will reply here once the studio
            backend is connected.
          </div>
        ) : (
          turns.map((turn) => (
            <article
              key={turn.id}
              data-role={turn.role}
              style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
            >
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                <span
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: STUDIO_ROLE_COLOR[turn.role],
                  }}
                >
                  {turn.label || STUDIO_ROLE_LABEL[turn.role]}
                </span>
                <time
                  dateTime={turn.at}
                  style={{ fontSize: 11, color: '#A8A299', fontVariantNumeric: 'tabular-nums' }}
                >
                  {formatTime(turn.at)}
                </time>
              </div>
              <div
                style={{
                  fontSize: 14,
                  lineHeight: 1.5,
                  color: '#2A2722',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {turn.text}
                {turn.streaming && (
                  <span
                    aria-label="streaming"
                    style={{
                      display: 'inline-block',
                      width: 7,
                      height: 14,
                      marginLeft: 2,
                      verticalAlign: 'text-bottom',
                      background: STUDIO_ROLE_COLOR[turn.role],
                      animation: 'studioCaret 1s steps(2) infinite',
                    }}
                  />
                )}
              </div>
            </article>
          ))
        )}
      </div>

      {/* Honest preview note */}
      {isPreview && (
        <div
          role="note"
          style={{
            margin: '0 16px',
            padding: '8px 12px',
            background: '#FEF9F5',
            border: '1px solid #F0E6D8',
            borderRadius: 9,
            fontSize: 12,
            lineHeight: 1.4,
            color: '#8B6F47',
          }}
        >
          Preview — not connected to live agents yet. Your messages are shown
          locally only; no agent will reply and nothing is sent.
        </div>
      )}

      {/* Composer */}
      <div
        style={{
          padding: '12px 16px',
          borderTop: '1px solid var(--hairline)',
          display: 'flex',
          gap: 8,
          alignItems: 'flex-end',
        }}
      >
        <textarea
          aria-label="Message the campaign team"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="Describe your campaign goal, audience, channels…"
          rows={2}
          disabled={busy}
          style={{
            flex: 1,
            resize: 'none',
            fontSize: 14,
            fontFamily: 'inherit',
            padding: '10px 12px',
            border: '1px solid var(--hairline)',
            borderRadius: 9,
            outline: 'none',
            background: '#fff',
            opacity: busy ? 0.6 : 1,
          }}
        />
        <button
          type="button"
          onClick={submit}
          disabled={busy || draft.trim().length === 0}
          style={{
            fontSize: 14,
            fontWeight: 600,
            padding: '10px 18px',
            border: 'none',
            borderRadius: 9,
            background: '#0F8A82',
            color: '#fff',
            cursor: busy || draft.trim().length === 0 ? 'not-allowed' : 'pointer',
            opacity: busy || draft.trim().length === 0 ? 0.5 : 1,
          }}
        >
          Send
        </button>
      </div>

      <style>{`
        @keyframes studioCaret { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }
      `}</style>
    </section>
  );
}
