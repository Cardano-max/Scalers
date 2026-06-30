'use client';

/**
 * VoiceScreen — the first-class, voice-first primary surface (the default landing).
 *
 * NET LAYOUT (operator's exact ask): the left sidebar/tree is owned by AppShell;
 * this screen lays out the remaining two panes:
 *   CENTER  — the big audio-reactive orb + status caption + live captions + the
 *             plan readback chips + a minimal text composer (type-first fallback).
 *             You talk first (or type); the host interviews you; on a spoken GO it
 *             spins up the team.
 *   RIGHT   — the LIVE per-agent reasoning stream (AgencyCanvas, compact): the real
 *             run trace rendered as the agency working — deep research, strategist,
 *             copywriters, critics re-verifying, the supervising jury, then the spec.
 *
 * Voice + the run are the SHARED studio instance (StudioRunProvider), so this view
 * and the Agency tab watch the SAME run. Voice is SEND-INCAPABLE; the only publish
 * path is the Review-Queue approve. Honest empty/not-connected states throughout.
 */
import { useConsole } from '@/state/console-store';
import { useSharedStudio } from '@/lib/studio/StudioRunProvider';
import { useVoiceHost } from '@/lib/studio/voice/useVoiceHost';
import { VoiceOrb } from './VoiceOrb';
import { AgencyCanvas } from './AgencyCanvas';
import { VoiceTweakPanel } from './VoiceTweakPanel';
import type { ChatTurn } from '@/lib/data/studio-adapter';

const HOST_ACCENT = '#6D4AE6';
const TEAL = '#0F8A82';

/** Derive a sibling studio route (e.g. /upload, /notes) from the AG-UI URL. */
function studioRoute(aguiUrl: string, suffix: string): string {
  return aguiUrl ? aguiUrl.replace(/\/agui(\?.*)?$/, suffix) : '';
}

/** Caption for each real voice state — never implies a state the session isn't in. */
function caption(conn: string, awaitingGo: boolean, disabled: boolean): string {
  if (disabled) return 'Microphone / backend unavailable — type below instead.';
  switch (conn) {
    case 'minting':
    case 'connecting':
      return 'Connecting…';
    case 'live':
      return awaitingGo ? "Say “go ahead” to spin up the team." : 'Interviewing you…';
    case 'error':
      return 'Voice error — you can still type below.';
    case 'closed':
    case 'idle':
    default:
      return 'Tap to talk to your strategist.';
  }
}

export function VoiceScreen() {
  const studio = useSharedStudio();
  const { navigate } = useConsole();
  const connected = studio.connected === true;

  const voice = useVoiceHost(studio.aguiUrl, studio.sessionId, studio.runState, {
    disabled: !connected,
    onRunLaunched: studio.attachRun,
    onPlan: (p) => studio.setPlanField(p as Partial<typeof studio.plan>),
    // Spoken turns flow into the SAME transcript as typed turns — one conversation,
    // one session id (studio.sessionId). Talking and typing continue each other.
    onUserFinal: (t) => studio.recordVoiceTurn('OPERATOR', 'You', t),
    onAssistantFinal: (t) => studio.recordVoiceTurn('SYSTEM', 'Studio Host', t),
  });

  const orbDisabled = !connected;
  const cap = caption(voice.conn, voice.awaitingGo, orbDisabled);

  // The center thread is the ONE conversation (operator + host) — interleaving what
  // you TYPED (studio.send) and what you SAID (recordVoiceTurn), both on the same
  // session. Per-agent run steps live exclusively in the right reasoning stream, so
  // the center stays calm and minimal.
  const convoTurns: ChatTurn[] = studio.turns.filter(
    (t) => t.role === 'OPERATOR' || t.role === 'SYSTEM',
  );

  // Real upload routes — sit next to /studio/agui. Passed only when connected so the
  // controls show the honest not-connected note in preview instead of failing.
  const uploadEndpoint = connected ? studioRoute(studio.aguiUrl, '/upload') : undefined;
  const notesEndpoint = connected ? studioRoute(studio.aguiUrl, '/notes') : undefined;
  const documentsEndpoint = connected ? studioRoute(studio.aguiUrl, '/documents') : undefined;

  const plan = studio.plan;
  const planChips: { label: string; value: string }[] = [
    plan.goal ? { label: 'Goal', value: plan.goal } : null,
    plan.audience ? { label: 'Audience', value: plan.audience } : null,
    plan.channels.length ? { label: 'Channels', value: plan.channels.join(', ') } : null,
  ].filter(Boolean) as { label: string; value: string }[];

  return (
    <section
      aria-label="Voice studio"
      style={{ position: 'absolute', inset: 0, display: 'flex', minHeight: 0 }}
    >
      {/* CENTER — the voice hero + conversation. */}
      <div
        style={{
          flex: 1,
          minWidth: 0,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'auto',
          borderRight: '1px solid var(--hairline)',
          background: 'var(--canvas)',
        }}
      >
        {/* Hero — the orb breathes in lots of whitespace. */}
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 18,
            padding: '72px 24px 48px',
          }}
        >
          <VoiceOrb
            connState={voice.conn}
            awaitingGo={voice.awaitingGo}
            hostSpeaking={voice.hostSpeaking}
            micStream={voice.micStream}
            remoteStream={voice.remoteStream}
            size={188}
            disabled={orbDisabled}
            onClick={voice.live || voice.busy ? voice.stop : voice.start}
            ariaLabel={voice.live ? 'Stop voice session' : 'Start voice session'}
          />

          <div style={{ textAlign: 'center', display: 'flex', flexDirection: 'column', gap: 6, maxWidth: 460 }}>
            <div
              style={{
                fontSize: 15.5,
                fontWeight: 510,
                color: voice.awaitingGo ? TEAL : 'var(--ink)',
                letterSpacing: '-0.01em',
              }}
            >
              {cap}
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>
              {voice.live
                ? 'gpt-realtime · nothing is sent — every draft stays HELD for your approval'
                : 'Speak, or type below. Voice and text are equal.'}
            </div>
          </div>

          {/* Plan readback chips — the interview converging, from the real update_plan state. */}
          {(planChips.length > 0 || voice.readback) && (
            <div
              className="spring-in"
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: 8,
                justifyContent: 'center',
                maxWidth: 560,
                background: voice.awaitingGo ? 'var(--success-bg)' : 'var(--surface)',
                border: `1px solid ${voice.awaitingGo ? '#C7E8D4' : 'var(--hairline)'}`,
                borderRadius: 'var(--radius-card)',
                padding: '12px 14px',
              }}
            >
              {planChips.length > 0 ? (
                planChips.map((c) => (
                  <span
                    key={c.label}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 6,
                      fontSize: 12,
                      color: 'var(--ink)',
                      background: 'var(--surface-alt)',
                      border: '1px solid var(--hairline)',
                      borderRadius: 'var(--radius-pill)',
                      padding: '4px 11px',
                    }}
                  >
                    <span className="label" style={{ fontSize: 9 }}>{c.label}</span>
                    <span style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {c.value}
                    </span>
                  </span>
                ))
              ) : (
                <span style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>
                  <strong style={{ color: HOST_ACCENT }}>Plan:</strong> {voice.readback}
                </span>
              )}
            </div>
          )}

          {/* Transient live caption — the host's current spoken line WHILE it talks.
              Finalized turns (yours and the host's) land in the single transcript
              below via recordVoiceTurn, so no line is shown in two places. */}
          {voice.hostSpeaking && voice.assistantLine && (
            <div style={{ width: '100%', maxWidth: 560, fontSize: 13, color: 'var(--ink)' }}>
              <strong style={{ color: HOST_ACCENT }}>Host:</strong> {voice.assistantLine}
            </div>
          )}

          {voice.error && (
            <div role="alert" style={{ fontSize: 12.5, color: 'var(--danger-text)' }}>
              {voice.error}
            </div>
          )}
        </div>

        {/* The light transcript + small "tweak" box + uploads (not a heavy chat wall). */}
        <div style={{ flex: 1, minHeight: 300, display: 'flex', padding: '0 24px 24px', maxWidth: 640, width: '100%', margin: '0 auto' }}>
          <VoiceTweakPanel
            turns={convoTurns}
            onSend={studio.send}
            streamStatus={studio.streamStatus}
            busy={studio.busy}
            approval={
              studio.pendingApproval
                ? {
                    toolName: studio.pendingApproval.call.name,
                    args: studio.pendingApproval.call.args || '{}',
                    message: studio.pendingApproval.interrupt.message,
                  }
                : null
            }
            onApprove={studio.approve}
            onReject={studio.reject}
            uploadEndpoint={uploadEndpoint}
            notesEndpoint={notesEndpoint}
            documentsEndpoint={documentsEndpoint}
            sessionId={studio.sessionId}
          />
        </div>
      </div>

      {/* RIGHT — the live per-agent reasoning stream (the agency at work). */}
      <aside
        aria-label="Agency reasoning stream"
        style={{
          width: 'clamp(360px, 36vw, 480px)',
          flex: '0 0 auto',
          overflow: 'auto',
          background: 'var(--warroom-canvas)',
          padding: 16,
        }}
      >
        {/* No bare Run button here: on the Voice tab the run starts through the voice
            interview + server-side GO-gate (request_orchestration). The canvas only
            WATCHES that held run — it never auto-runs blindly. */}
        <AgencyCanvas
          runState={studio.runState}
          running={studio.runningCampaign}
          connected={connected}
          compact
          onOpenReview={() => navigate('review')}
          onDeepReview={(actionId) => navigate('review', actionId)}
        />
      </aside>
    </section>
  );
}
