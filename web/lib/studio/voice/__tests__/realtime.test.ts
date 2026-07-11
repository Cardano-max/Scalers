/**
 * Voice relay tests — the browser tool router NEVER sends/publishes.
 *
 * Proves: update_plan routes to /studio/voice/plan; request_orchestration routes to
 * /studio/voice/orchestrate carrying the latest spoken transcript (the server enforces
 * the GO-gate); and any non-routable tool name (e.g. a hallucinated publish/send) is
 * refused with no network call at all — there is no send path in the browser.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { routeToolCall, ROUTABLE_TOOLS, studioBase } from '../realtime';

const AGUI = 'http://localhost:8000/studio/agui';

function mockJson(body: unknown) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) } as Response);
}

describe('voice tool router', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('exposes exactly the routable tools (no publish/send)', () => {
    // Two write-shaped (plan edit + gated launch request) + two READ-ONLY
    // (get_run_status + list_conversation_leads) — still no publish/send handler
    // anywhere in the browser.
    expect(ROUTABLE_TOOLS).toEqual([
      'update_plan',
      'request_orchestration',
      'get_run_status',
      'list_conversation_leads',
    ]);
  });

  it('routes list_conversation_leads to the read-only leads route', async () => {
    fetchMock.mockReturnValueOnce(
      mockJson({ ok: true, leads: [{ name: 'Amanda Kuhl' }], count: 1 }),
    );
    const out = await routeToolCall('list_conversation_leads', '{"topic":"price"}', {
      aguiUrl: 'http://x/studio/agui',
      sessionId: 's1',
      latestTranscript: () => '',
    });
    expect(out.ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      'http://x/studio/voice/leads',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('routes get_run_status to the read-only run-state route', async () => {
    fetchMock.mockReturnValueOnce(
      mockJson({ ok: true, runId: 'team-camp_x', drafts: { drafts: [] } }),
    );
    const out = await routeToolCall('get_run_status', '{}', {
      aguiUrl: AGUI,
      sessionId: 'sess1',
      latestTranscript: () => '',
    });
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/studio/voice/run_status',
      expect.objectContaining({ method: 'POST' }),
    );
    expect(out.ok).toBe(true);
    expect(out.runId).toBe('team-camp_x');
  });

  it('studioBase strips the trailing /agui', () => {
    expect(studioBase(AGUI)).toBe('http://localhost:8000/studio');
  });

  it('routes update_plan to /voice/plan and never launches', async () => {
    fetchMock.mockReturnValueOnce(
      mockJson({ ok: true, plan: { goal: 'g' }, awaitingGo: false, readback: 'Goal: g.' }),
    );
    const out = await routeToolCall('update_plan', JSON.stringify({ goal: 'g' }), {
      aguiUrl: AGUI,
      sessionId: 's1',
      latestTranscript: () => '',
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('http://localhost:8000/studio/voice/plan');
    expect(out.ok).toBe(true);
    expect(out.awaiting_go).toBe(false);
    expect('launched' in out).toBe(false);
  });

  it('routes request_orchestration to /voice/orchestrate carrying the transcript', async () => {
    fetchMock.mockReturnValueOnce(
      mockJson({ ok: true, launched: true, runId: 'team-xyz', gate: { launch: true } }),
    );
    const out = await routeToolCall('request_orchestration', '{}', {
      aguiUrl: AGUI,
      sessionId: 's1',
      latestTranscript: () => 'run it',
    });
    expect(fetchMock.mock.calls[0][0]).toBe('http://localhost:8000/studio/voice/orchestrate');
    const sentBody = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(sentBody.transcript).toBe('run it');
    expect(out.launched).toBe(true);
    expect(out.run_id).toBe('team-xyz');
  });

  it('surfaces a server GO-gate refusal honestly (no launch)', async () => {
    fetchMock.mockReturnValueOnce(
      mockJson({ ok: true, launched: false, gate: { launch: false, reason: 'not armed' } }),
    );
    const out = await routeToolCall('request_orchestration', '{}', {
      aguiUrl: AGUI,
      sessionId: 's1',
      latestTranscript: () => 'go ahead',
    });
    expect(out.launched).toBe(false);
    expect(out.reason).toBe('not armed');
  });

  it('refuses any non-routable tool name with NO network call (no send path)', async () => {
    for (const forbidden of ['publish', 'send_email', 'stage_publish', 'post']) {
      const out = await routeToolCall(forbidden, '{}', {
        aguiUrl: AGUI,
        sessionId: 's1',
        latestTranscript: () => 'go',
      });
      expect(out.ok).toBe(false);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
