'use client';

/**
 * CampaignStudio — the interactive Campaign Studio that replaces the static
 * CommandScreen brief form (ADR docs/adr/command-campaign-studio.md Decision 7).
 *
 * Owns the StudioAdapter + session state and lays out three panels:
 *   - StudioChatPanel    — operator + agent-role conversation (streaming-ready).
 *   - LiveProgressPanel  — per-agent steps as they run.
 *   - PlanDocPanel       — the editable plan/spec doc + Approve / Execute.
 *
 * HONESTY GATE (P2 scaffolding): the default adapter is the PreviewStudioAdapter
 * — explicitly NOT wired to live agents. Real, typed components render; no agent
 * conversation is fabricated. The stream emits nothing, agent steps stay empty,
 * and the mutating actions are disabled. Swap `createStudioAdapter()` for a live
 * adapter (P1/P2 backend) to light it up — no component change required.
 */
import { useEffect, useRef, useState } from 'react';
import {
  createStudioAdapter,
  type AgentStep,
  type ChatTurn,
  type PlanDoc,
  type StudioAdapter,
  type StudioStream,
  type StudioStreamStatus,
} from '@/lib/data/studio-adapter';
import { StudioChatPanel } from './StudioChatPanel';
import { LiveProgressPanel } from './LiveProgressPanel';
import { PlanDocPanel } from './PlanDocPanel';
import { LiveCampaignStudio } from './LiveCampaignStudio';

interface CampaignStudioProps {
  /** Override the adapter (tests inject a fake; default = preview, not wired). */
  adapter?: StudioAdapter;
  /** Studio session id; the real backend mints one per campaign session. */
  sessionId?: string;
}

/**
 * Dispatcher: decide LIVE (AG-UI) vs PREVIEW WITHOUT calling any hooks, so the
 * conditional return is safe under the rules-of-hooks. The live branch is selected
 * only when a studio AG-UI endpoint is configured AND no adapter is injected (tests
 * inject one / leave it preview). Each branch renders a component that owns its own
 * hooks unconditionally — so hook order is stable within each rendered subtree.
 */
export function CampaignStudio({
  adapter,
  sessionId = 'studio-preview-session',
}: CampaignStudioProps) {
  const aguiUrl =
    typeof process !== 'undefined' ? process.env.NEXT_PUBLIC_STUDIO_AGUI_URL : undefined;
  if (!adapter && aguiUrl) {
    const graphqlUrl =
      (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_STUDIO_GRAPHQL_URL) ||
      '/graphql';
    const liveSession =
      sessionId === 'studio-preview-session' ? 'studio-live-session' : sessionId;
    return (
      <LiveCampaignStudio aguiUrl={aguiUrl} graphqlUrl={graphqlUrl} sessionId={liveSession} />
    );
  }
  return <PreviewCampaignStudio adapter={adapter} sessionId={sessionId} />;
}

/**
 * PreviewCampaignStudio — the not-wired scaffold path (or any injected adapter,
 * including the test fake). Owns all the studio hooks unconditionally.
 */
function PreviewCampaignStudio({
  adapter,
  sessionId = 'studio-preview-session',
}: CampaignStudioProps) {
  // Resolve the adapter once; preview by default.
  const adapterRef = useRef<StudioAdapter>(adapter ?? createStudioAdapter());
  const studio = adapterRef.current;
  const isPreview = studio.source === 'preview';

  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [streamStatus, setStreamStatus] = useState<StudioStreamStatus>('connecting');

  const [planDoc, setPlanDoc] = useState<PlanDoc | null>(null);
  const [planBody, setPlanBody] = useState('');
  const [planLoading, setPlanLoading] = useState(true);

  // Subscribe to streamed agent turns + steps. In preview this reports
  // status 'preview' and emits nothing (no fabricated conversation).
  useEffect(() => {
    let stream: StudioStream | null = null;
    stream = studio.streamAgentTurns(sessionId, {
      onStatus: (s) => setStreamStatus(s),
      onTurn: (turn) =>
        setTurns((prev) =>
          prev.some((t) => t.id === turn.id)
            ? prev.map((t) => (t.id === turn.id ? turn : t))
            : [...prev, turn],
        ),
      onTurnDelta: (turnId, delta) =>
        setTurns((prev) =>
          prev.map((t) =>
            t.id === turnId ? { ...t, text: t.text + delta } : t,
          ),
        ),
      onStep: (step) =>
        setSteps((prev) =>
          prev.some((s) => s.id === step.id)
            ? prev.map((s) => (s.id === step.id ? step : s))
            : [...prev, step],
        ),
    });
    return () => stream?.close();
  }, [studio, sessionId]);

  // Load the plan/spec doc.
  useEffect(() => {
    let cancelled = false;
    setPlanLoading(true);
    studio
      .getPlanDoc(sessionId)
      .then((doc) => {
        if (cancelled) return;
        setPlanDoc(doc);
        setPlanBody(doc?.body ?? '');
      })
      .finally(() => {
        if (!cancelled) setPlanLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [studio, sessionId]);

  const handleSend = (text: string) => {
    // Append the operator's own message. In preview this is a local echo of
    // what they typed — no agent reply is fabricated (the stream emits none).
    studio
      .sendChatMessage(sessionId, text)
      .then((turn) => setTurns((prev) => [...prev, turn]))
      .catch(() => {
        /* preview/mutation seams may reject; surfaced by the honest note */
      });
  };

  return (
    <section
      aria-label="Campaign Studio"
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        flexDirection: 'column',
        gap: 14,
        padding: 20,
        minHeight: 0,
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <h1 style={{ margin: 0, fontSize: 20, fontWeight: 600, color: '#1A1A17' }}>
            Campaign Studio
          </h1>
          <p style={{ margin: 0, fontSize: 13, color: '#6B6461' }}>
            Co-create a campaign with the multi-agent team, then approve and run.
          </p>
        </div>
        <span
          aria-label="Studio source"
          style={{
            fontSize: 11,
            fontWeight: 600,
            textTransform: 'uppercase',
            letterSpacing: 0.5,
            padding: '4px 10px',
            borderRadius: 999,
            background: isPreview ? '#FBF0D9' : '#E6F4EC',
            color: isPreview ? '#9A6B00' : '#157F4B',
            border: `1px solid ${isPreview ? '#F0E0B8' : '#C7E8D4'}`,
          }}
        >
          {isPreview ? 'Preview · not wired' : 'Live'}
        </span>
      </div>

      {/* Top-level honesty banner */}
      {isPreview && (
        <div
          role="note"
          style={{
            padding: '10px 14px',
            background: '#FEF9F5',
            border: '1px solid #F0E6D8',
            borderRadius: 10,
            fontSize: 13,
            lineHeight: 1.45,
            color: '#8B6F47',
          }}
        >
          <strong>Preview — not connected to live agents yet.</strong> This is P2
          scaffolding: the panels are real and typed, but no campaign run, agent
          turn, or plan approval is wired to a backend. Everything you see is an
          honest empty/preview state — nothing is fabricated.
        </div>
      )}

      {/* Body: chat + progress (left) · plan doc (right) */}
      <div style={{ flex: 1, minHeight: 0, display: 'flex', gap: 14 }}>
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 14, minHeight: 0 }}>
          <StudioChatPanel turns={turns} onSend={handleSend} streamStatus={streamStatus} />
          <LiveProgressPanel steps={steps} streamStatus={streamStatus} />
        </div>
        <div style={{ width: 400, flex: '0 0 400px', display: 'flex', minHeight: 0 }}>
          <PlanDocPanel
            doc={planDoc}
            loading={planLoading}
            body={planBody}
            onChangeBody={setPlanBody}
            notWired={isPreview}
          />
        </div>
      </div>
    </section>
  );
}
