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
import { MicButton } from './MicButton';
import { appendTranscript, type SttFactoryOptions } from '@/lib/studio/stt';

/** A would-send action paused at the approval gate, surfaced in-thread. */
export interface ChatApproval {
  toolName: string;
  args: string;
  message?: string;
}

interface StudioChatPanelProps {
  turns: ChatTurn[];
  onSend: (text: string) => void;
  /** Stream lifecycle; 'preview' drives the honest not-connected note. */
  streamStatus: StudioStreamStatus;
  busy?: boolean;
  /** When set, render an explicit Approve/Reject card in the thread (HITL gate). */
  approval?: ChatApproval | null;
  onApprove?: () => void;
  onReject?: () => void;
  /**
   * STT engine options for the voice mic (DI seam for tests; omit in the app to
   * use the auto-selected browser engine). See lib/studio/stt.
   */
  micOptions?: SttFactoryOptions;
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
  approval = null,
  onApprove,
  onReject,
  micOptions,
}: StudioChatPanelProps) {
  const [draft, setDraft] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);

  // Voice input drops the FINAL transcript into the existing draft; the same
  // text path (submit -> onSend) handles it. Nothing else changes.
  const handleTranscript = (text: string) => {
    setDraft((prev) => appendTranscript(prev, text));
  };

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

      {/* Approval gate (HITL): an explicit Approve/Reject card in the thread. The
          would-send is STAGED on approve (held, never auto-fired); reject denies it. */}
      {approval && (
        <div
          role="alertdialog"
          aria-label="Approval required"
          style={{
            margin: '0 16px 4px',
            padding: '12px 14px',
            background: '#FFF7ED',
            border: '1px solid #F0C99A',
            borderRadius: 10,
          }}
        >
          <div style={{ fontSize: 12, fontWeight: 700, color: '#B45309', marginBottom: 4 }}>
            Approval required — nothing is sent until you approve
          </div>
          <div style={{ fontSize: 13, color: '#7C2D12', lineHeight: 1.45, marginBottom: 8 }}>
            {approval.message ?? `The agent wants to run ${approval.toolName}.`}
          </div>
          <div
            style={{
              fontSize: 12,
              fontFamily: "'IBM Plex Mono', monospace",
              color: '#92400E',
              background: '#FFFBEB',
              border: '1px solid #FDE8C8',
              borderRadius: 7,
              padding: '6px 8px',
              marginBottom: 10,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {approval.toolName}({approval.args})
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              type="button"
              onClick={onApprove}
              disabled={busy}
              style={{
                fontSize: 13,
                fontWeight: 600,
                padding: '8px 16px',
                border: 'none',
                borderRadius: 8,
                background: '#0F8A82',
                color: '#fff',
                cursor: busy ? 'not-allowed' : 'pointer',
                opacity: busy ? 0.6 : 1,
              }}
            >
              Approve &amp; stage (held)
            </button>
            <button
              type="button"
              onClick={onReject}
              disabled={busy}
              style={{
                fontSize: 13,
                fontWeight: 600,
                padding: '8px 16px',
                border: '1px solid #E2A66B',
                borderRadius: 8,
                background: '#fff',
                color: '#9A3412',
                cursor: busy ? 'not-allowed' : 'pointer',
                opacity: busy ? 0.6 : 1,
              }}
            >
              Reject
            </button>
          </div>
        </div>
      )}

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
        <MicButton onTranscript={handleTranscript} disabled={busy} sttOptions={micOptions} />
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
