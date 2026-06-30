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
import { MicButton } from './MicButton';
import { CustomerUpload } from './CustomerUpload';
import { BrandNotesUpload } from './BrandNotesUpload';
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
  approval?: VoiceTweakApproval | null;
  onApprove?: () => void;
  onReject?: () => void;
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
  approval = null,
  onApprove,
  onReject,
  uploadEndpoint,
  notesEndpoint,
  documentsEndpoint,
  sessionId,
  micOptions,
}: VoiceTweakPanelProps) {
  const [draft, setDraft] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
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
        {turns.length === 0 ? (
          <p style={{ margin: 'auto 0', textAlign: 'center', fontSize: 12.5, lineHeight: 1.55, color: 'var(--text-muted)', maxWidth: 380, alignSelf: 'center' }}>
            Talk, or type a brief below. The strategist replies here, remembers the
            conversation, and orchestrates the team once the plan is set.
          </p>
        ) : (
          turns.map((turn) => <TranscriptLine key={turn.id} turn={turn} />)
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
          out the transcript. */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          flexWrap: 'wrap',
          minWidth: 0,
        }}
      >
        <span style={{ fontSize: 11, color: 'var(--text-faint)', marginRight: 'auto' }}>
          Add context the team will use:
        </span>
        <CustomerUpload endpoint={uploadEndpoint} sessionId={sessionId} />
        <BrandNotesUpload endpoint={notesEndpoint} sessionId={sessionId} />
      </div>

      {/* Persistent knowledge store — the docs every agent reads (host, run, voice).
          Collapsed to a summary on the Voice surface; expand to upload / manage. */}
      <KnowledgePanel endpoint={documentsEndpoint} collapsible />

      <style>{`@keyframes studioCaret { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }`}</style>
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
              background: persona.accent,
              animation: 'studioCaret 1s steps(2) infinite',
            }}
          />
        )}
      </div>
    </div>
  );
}
