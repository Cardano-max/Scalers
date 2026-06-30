'use client';

/**
 * LiveCampaignStudio — the Campaign Studio bound to the LIVE AG-UI backend.
 *
 * Rendered by CampaignStudio when a studio AG-UI endpoint is configured. It drives
 * the real `useStudioAgui` state machine: the chat sends operator turns to the
 * Studio Host (real model round-trips), the editable plan is the AG-UI shared
 * state (edits sync back and the host re-plans), the labeled role brainstorm comes
 * from persisted history, and a would-send pauses at an in-thread approval gate.
 *
 * HONESTY: if the backend is unreachable the hook reports `streamStatus: 'preview'`
 * and `connected: false`; this component then renders the SAME honest not-connected
 * state as the preview adapter (banner + disabled actions), never a fake exchange.
 */
import type { PlanDoc } from '@/lib/data/studio-adapter';
import { useStudioAgui } from '@/lib/studio/useStudioAgui';
import { StudioChatPanel } from './StudioChatPanel';
import { LiveProgressPanel } from './LiveProgressPanel';
import { PlanDocPanel } from './PlanDocPanel';

interface LiveCampaignStudioProps {
  aguiUrl: string;
  graphqlUrl: string;
  sessionId: string;
}

export function LiveCampaignStudio({ aguiUrl, graphqlUrl, sessionId }: LiveCampaignStudioProps) {
  const studio = useStudioAgui(aguiUrl, graphqlUrl, sessionId);
  const connected = studio.connected === true;
  // While probing (connected === null) treat as connecting; failed probe => preview.
  const isPreview = studio.connected === false;
  // The customers-CSV upload endpoint lives next to /studio/agui on the same backend.
  const uploadEndpoint = aguiUrl.replace(/\/agui(\?.*)?$/, '/upload');

  const planDoc: PlanDoc = {
    id: `plan_live_${sessionId}`,
    sessionId,
    version: studio.planVersion,
    title: 'Campaign plan (live shared state)',
    body: '',
    status: 'draft',
    updatedAt: new Date().toISOString(),
  };

  const approval = studio.pendingApproval
    ? {
        toolName: studio.pendingApproval.call.name,
        args: studio.pendingApproval.call.args || '{}',
        message: studio.pendingApproval.interrupt.message,
      }
    : null;

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
          {studio.connected === null ? 'Connecting…' : isPreview ? 'Preview · backend unreachable' : 'Live'}
        </span>
      </div>

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
          <strong>Preview — the studio backend is not reachable.</strong> The console
          is configured for the live AG-UI studio but could not reach it, so this is
          the honest not-connected state: no agent will reply and nothing is sent.
        </div>
      )}

      {studio.error && !isPreview && (
        <div
          role="alert"
          style={{
            padding: '10px 14px',
            background: '#FEF2F2',
            border: '1px solid #FCA5A5',
            borderRadius: 10,
            fontSize: 13,
            color: '#B42318',
          }}
        >
          Backend error: {studio.error}
        </div>
      )}

      <div style={{ flex: 1, minHeight: 0, display: 'flex', gap: 14 }}>
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 14, minHeight: 0 }}>
          <StudioChatPanel
            turns={studio.turns}
            onSend={studio.send}
            streamStatus={studio.streamStatus}
            busy={studio.busy}
            approval={approval}
            onApprove={studio.approve}
            onReject={studio.reject}
            uploadEndpoint={connected ? uploadEndpoint : undefined}
          />
          <LiveProgressPanel steps={studio.steps} streamStatus={studio.streamStatus} />
        </div>
        <div style={{ width: 400, flex: '0 0 400px', display: 'flex', minHeight: 0 }}>
          <PlanDocPanel
            doc={planDoc}
            loading={studio.connected === null}
            body=""
            onChangeBody={() => {}}
            notWired={isPreview}
            plan={connected ? studio.plan : null}
            onChangeField={studio.setPlanField}
            onApplyReplan={studio.applyEditsAndReplan}
            planDirty={studio.planDirty}
            busy={studio.busy}
            onRunCampaign={connected ? studio.runCampaign : undefined}
            running={studio.runningCampaign}
          />
        </div>
      </div>
    </section>
  );
}
