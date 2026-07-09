'use client';

/**
 * VoiceTweakPanel — the LIGHT center surface beneath the voice orb.
 *
 * The operator asked to kill the bulky chat-thread wall: the center is voice-first
 * (the orb above) plus a small text "tweak" box, the customer CSV + brand-notes
 * uploads near the input, and an ELEGANT, light transcript — not a heavy chat panel.
 *
 * It is presentational over the SHARED studio run: it renders exactly the turns it
 * is given (operator + host), streams a live caret on an in-flight host turn, and
 * surfaces the HITL approval gate. The composer routes to `onSend` — the SAME
 * stateful, in-session path the voice host uses, so "make it softer" / "rewrite the
 * email" revise in context. Nothing here sends; the only publish path is Approve.
 */
import { useEffect, useRef, useState } from 'react';
import type { ChatTurn } from '@/lib/data/studio-adapter';
import type { StudioStreamStatus } from '@/lib/data/studio-adapter';
import { studioPersona } from '@/lib/studio/persona';
import { renderMarkdown } from '@/lib/markdown';
import { MicButton } from './MicButton';
import { CustomerUpload } from './CustomerUpload';
import { BrandNotesUpload } from './BrandNotesUpload';
import { MediaUpload } from './MediaUpload';
import { KnowledgePanel } from './KnowledgePanel';
import type { SttFactoryOptions } from '@/lib/studio/stt';

export interface VoiceTweakApproval {
  toolName: string;
  args: string;
  message?: string;
}

interface VoiceTweakPanelProps {
  turns: ChatTurn[];
  onSend: (text: string) => void;
  streamStatus: StudioStreamStatus;
  busy?: boolean;
  /** True while a typed message is awaiting the host's first token (QA 5e) —
   *  drives the "thinking" indicator until the reply starts streaming. */
  pendingReply?: boolean;
  approval?: VoiceTweakApproval | null;
  onApprove?: () => void;
  onReject?: () => void;
  /** A live voice session is connected (drives the in-transcript listening row). */
  live?: boolean;
  /** The host is currently speaking (its partial line streams into the transcript). */
  hostSpeaking?: boolean;
  /** The host's in-flight spoken line (partial transcription, before it finalizes). */
  liveHostLine?: string;
  /** POST /studio/upload — customer CSV (omit in preview to show the honest note). */
  uploadEndpoint?: string;
  /** POST /studio/notes — brand / strategy notes (omit in preview). */
  notesEndpoint?: string;
  /** /studio/documents — persistent knowledge store (omit in preview). */
  documentsEndpoint?: string;
  sessionId: string;
  micOptions?: SttFactoryOptions;
}

export function VoiceTweakPanel({
  turns,
  onSend,
  streamStatus,
  busy = false,
  pendingReply = false,
  approval = null,
  onApprove,
  onReject,
  live = false,
  hostSpeaking = false,
  liveHostLine = '',
  uploadEndpoint,
  notesEndpoint,
  documentsEndpoint,
  sessionId,
  micOptions,
}: VoiceTweakPanelProps) {
  const [draft, setDraft] = useState('');
  // Bumped by an upload/notes success so the Context (documents) panel below
  // re-fetches instead of staying on a stale "No documents yet" (QA 5h).
  const [contextVersion, setContextVersion] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);

  // The host is "thinking" from Send until its first token/reply arrives (QA 5e).
  const replyStreaming = turns.some((t) => t.streaming);
  const showThinking = pendingReply && !replyStreaming;

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [turns, liveHostLine, hostSpeaking, live, showThinking]);

  // The transcript is "alive" whenever a voice session is connected OR there is at
  // least one turn — so the listening/partial row can show even before the first
  // finalized turn lands.
  const showLiveRow = live;

  const isPreview = streamStatus === 'preview';

  const submit = () => {
    const text = draft.trim();
    if (!text || busy) return;
    onSend(text);
    setDraft('');
  };

  return (
    <section
      aria-label="Tweak and transcript"
      style={{ display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0, flex: 1, gap: 12 }}
    >
      {/* PRIMARY — the live transcript. It owns the vertical space and scrolls inside
          itself so it is ALWAYS visible: user speech, the host's replies, the live
          streaming caret. Everything else (composer, uploads, context) sits below it
          and never pushes it off-screen. */}
      <div
        ref={scrollRef}
        role="log"
        aria-live="polite"
        aria-label="Conversation transcript"
        style={{
          flex: 1,
          minHeight: 160,
          overflowY: 'auto',
          overflowX: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          gap: 14,
          padding: '8px 4px',
        }}
      >
        {turns.length === 0 && !showLiveRow ? (
          <p style={{ margin: 'auto 0', textAlign: 'center', fontSize: 12.5, lineHeight: 1.55, color: 'var(--text-muted)', maxWidth: 380, alignSelf: 'center' }}>
            Talk, or type a brief below. The strategist replies here, remembers the
            conversation, and orchestrates the team once the plan is set.
          </p>
        ) : (
          turns.map((turn) => <TranscriptLine key={turn.id} turn={turn} />)
        )}

        {/* Thinking indicator — from Send until the host's first token/reply lands
            (QA 5e). Removed the moment a streaming turn or the reply appears. */}
        {showThinking && (
          <div
            role="status"
            aria-label="Studio Host is thinking"
            style={{ alignSelf: 'flex-start', display: 'flex', alignItems: 'center', gap: 8 }}
          >
            <span style={{ fontSize: 10.5, fontWeight: 700, color: '#6D4AE6' }}>Studio Host</span>
            <span style={{ display: 'inline-flex', gap: 3 }}>
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  aria-hidden
                  style={{
                    width: 5,
                    height: 5,
                    borderRadius: '50%',
                    background: '#6D4AE6',
                    opacity: 0.5,
                    animation: 'studioThinking 1.1s ease-in-out infinite',
                    animationDelay: `${i * 0.18}s`,
                  }}
                />
              ))}
            </span>
          </div>
        )}

        {/* Live row — the host's in-flight spoken line (partial transcription) while it
            talks, or a calm Listening indicator while the session is open and quiet. It
            keeps the transcript visibly alive between finalized turns. */}
        {showLiveRow && (
          hostSpeaking && liveHostLine.trim() ? (
            <div data-role="LIVE_HOST" style={{ alignSelf: 'flex-start', maxWidth: '88%', display: 'flex', flexDirection: 'column', gap: 3 }}>
              <span style={{ fontSize: 10.5, fontWeight: 700, color: '#6D4AE6' }}>Studio Host</span>
              <div style={{ fontSize: 13.5, lineHeight: 1.55, color: 'var(--ink)', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {liveHostLine}
                <span
                  aria-label="speaking"
                  style={{
                    display: 'inline-block',
                    width: 7,
                    height: 14,
                    marginLeft: 2,
                    verticalAlign: 'text-bottom',
                    background: '#6D4AE6',
                    animation: 'studioCaret 1s steps(2) infinite',
                  }}
                />
              </div>
            </div>
          ) : (
            <div role="status" style={{ alignSelf: 'flex-start', display: 'flex', alignItems: 'center', gap: 7, fontSize: 12, color: 'var(--text-muted)' }}>
              <span
                aria-hidden="true"
                style={{ width: 8, height: 8, borderRadius: '50%', background: '#0F8A82', animation: 'micPulse 1.2s ease-in-out infinite' }}
              />
              Listening…
            </div>
          )
        )}
      </div>

      {/* HITL approval gate — compact, never auto-fires. */}
      {approval && (
        <div
          role="alertdialog"
          aria-label="Approval required"
          style={{
            padding: '10px 12px',
            background: 'var(--amber-bg)',
            border: '1px solid var(--amber-border)',
            borderRadius: 10,
          }}
        >
          <div style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--amber-text)', marginBottom: 4 }}>
            Approval required — nothing is sent until you approve
          </div>
          <div style={{ fontSize: 12.5, color: '#7C2D12', lineHeight: 1.45, marginBottom: 8 }}>
            {approval.message ?? `The agent wants to run ${approval.toolName}.`}
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              type="button"
              onClick={onApprove}
              disabled={busy}
              style={{
                fontSize: 12.5,
                fontWeight: 600,
                padding: '7px 14px',
                border: 'none',
                borderRadius: 'var(--radius-button)',
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
                fontSize: 12.5,
                fontWeight: 600,
                padding: '7px 14px',
                border: '1px solid #E2A66B',
                borderRadius: 'var(--radius-button)',
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

      {isPreview && (
        <div
          role="note"
          style={{
            padding: '8px 12px',
            background: 'var(--surface)',
            border: '1px solid var(--hairline)',
            borderRadius: 9,
            fontSize: 11.5,
            lineHeight: 1.4,
            color: 'var(--text-muted)',
          }}
        >
          Preview — not connected to live agents. Your message shows locally only; no
          agent replies and nothing is sent.
        </div>
      )}

      {/* The small "tweak" composer — sits directly under the transcript. */}
      <div
        style={{
          display: 'flex',
          gap: 8,
          alignItems: 'flex-end',
          minWidth: 0,
          background: '#fff',
          border: '1px solid var(--hairline)',
          borderRadius: 12,
          padding: 8,
          boxShadow: 'var(--shadow-card)',
        }}
      >
        <textarea
          aria-label="Tweak the plan or type a brief"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="Type a brief, or tweak — e.g. “make it softer”, “rewrite the email”…"
          rows={1}
          disabled={busy}
          style={{
            flex: 1,
            resize: 'none',
            fontSize: 14,
            fontFamily: 'inherit',
            lineHeight: 1.4,
            padding: '8px 10px',
            border: 'none',
            outline: 'none',
            background: 'transparent',
            color: 'var(--ink)',
            opacity: busy ? 0.6 : 1,
          }}
        />
        <MicButton onTranscript={(t) => setDraft((p) => (p ? `${p} ${t}` : t))} disabled={busy} sttOptions={micOptions} />
        <button
          type="button"
          onClick={submit}
          disabled={busy || draft.trim().length === 0}
          style={{
            fontSize: 13.5,
            fontWeight: 600,
            padding: '9px 16px',
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

      {/* Below the composer: REAL context controls (CSV leads + brand/strategy notes)
          and the persistent knowledge store, collapsed by default so it never crowds
          out the transcript. Top-aligned so a success/error ack under a button can
          grow downward without re-centering the whole footer row (QA 5h). */}
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 12,
          flexWrap: 'wrap',
          minWidth: 0,
        }}
      >
        <span style={{ fontSize: 11, color: 'var(--text-faint)', marginRight: 'auto', paddingTop: 5 }}>
          Add context the team will use:
        </span>
        <CustomerUpload
          endpoint={uploadEndpoint}
          sessionId={sessionId}
          onUploaded={() => setContextVersion((v) => v + 1)}
        />
        <BrandNotesUpload
          endpoint={notesEndpoint}
          sessionId={sessionId}
          onUploaded={() => setContextVersion((v) => v + 1)}
        />
      </div>

      {/* Media intake on the Voice surface: images AND videos → artifact library,
          b-roll candidates + artist memory via the engine's VLM pipeline. */}
      <MediaUpload onUploaded={() => setContextVersion((v) => v + 1)} />

      {/* Persistent knowledge store — the docs every agent reads (host, run, voice).
          Collapsed to a summary on the Voice surface; expand to upload / manage.
          refreshToken re-fetches the list after an upload succeeds (QA 5h). */}
      <KnowledgePanel endpoint={documentsEndpoint} collapsible refreshToken={contextVersion} />

      <style>{`
        @keyframes studioCaret { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }
        @keyframes micPulse { 0%, 100% { transform: scale(1); opacity: 1; } 50% { transform: scale(1.5); opacity: 0.5; } }
        @keyframes studioThinking { 0%, 100% { opacity: 0.35; transform: translateY(0); } 50% { opacity: 1; transform: translateY(-2px); } }
      `}</style>
    </section>
  );
}

/** One transcript line — operator right, host left, calm and minimal. */
function TranscriptLine({ turn }: { turn: ChatTurn }) {
  const persona = studioPersona(turn);
  const isOperator = persona.side === 'right';
  return (
    <div
      data-role={turn.role}
      style={{
        alignSelf: isOperator ? 'flex-end' : 'flex-start',
        maxWidth: '88%',
        display: 'flex',
        flexDirection: 'column',
        gap: 3,
      }}
    >
      <span style={{ fontSize: 10.5, fontWeight: 700, color: persona.accent, textAlign: isOperator ? 'right' : 'left' }}>
        {isOperator ? 'You' : turn.label || persona.name}
      </span>
      <div
        style={{
          fontSize: 13.5,
          lineHeight: 1.55,
          color: isOperator ? '#0B3F3B' : 'var(--ink)',
          background: isOperator ? persona.bg : 'transparent',
          border: isOperator ? `1px solid ${persona.border}` : 'none',
          borderRadius: 12,
          padding: isOperator ? '8px 12px' : '0',
          // Operator lines stay verbatim (pre-wrap); host/agent lines render basic
          // markdown (bold/italic/lists/breaks) instead of raw asterisks (QA 5d).
          whiteSpace: isOperator ? 'pre-wrap' : 'normal',
          wordBreak: 'break-word',
        }}
      >
        {isOperator ? turn.text : renderMarkdown(turn.text)}
        {turn.streaming && (
          <span
            aria-label="streaming"
            style={{
              display: 'inline-block',
              width: 7,
              height: 14,
              marginLeft: 2,
              verticalAlign: 'text-bottom',
              background: persona.accent,
              animation: 'studioCaret 1s steps(2) infinite',
            }}
          />
        )}
      </div>
    </div>
  );
}
