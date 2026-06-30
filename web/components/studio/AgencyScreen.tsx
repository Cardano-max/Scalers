'use client';

/**
 * AgencyScreen — the full-screen "Agency at Work" view. It renders the SAME shared
 * run as the Voice tab's right rail (StudioRunProvider), so a run you kicked off by
 * voice continues to play here without rebuilding. Pure presentation over the real
 * run trace: deep research → strategist → copywriters drafting → critics
 * re-verifying → supervising jury → the campaign spec artifact. Honest empty state
 * (idle CTA) when no run has started; nothing here sends.
 */
import { useConsole } from '@/state/console-store';
import { useSharedStudio } from '@/lib/studio/StudioRunProvider';
import { AgencyCanvas } from './AgencyCanvas';

export function AgencyScreen() {
  const studio = useSharedStudio();
  const { navigate } = useConsole();
  const connected = studio.connected === true;

  return (
    <section
      aria-label="Agency at work"
      style={{ position: 'absolute', inset: 0, overflow: 'auto', padding: 24, background: 'var(--warroom-canvas)' }}
    >
      <div style={{ maxWidth: 1180, margin: '0 auto' }}>
        <AgencyCanvas
          runState={studio.runState}
          running={studio.runningCampaign}
          connected={connected}
          onRunCampaign={connected ? studio.runCampaign : undefined}
          onOpenReview={() => navigate('review')}
        />
      </div>
    </section>
  );
}
