import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { MockAdapter, MOCK_TENANT_ID } from '../mock-adapter';
import { SSE_EVENT_NAMES } from '../sse';

describe('MockAdapter — kkg.4 contract shapes', () => {
  const a = new MockAdapter();

  it('is labeled "mock"', () => {
    expect(a.source).toBe('mock');
  });

  it('getOverview returns the typed Overview composite', async () => {
    const ov = await a.getOverview(MOCK_TENANT_ID);
    expect(ov.kpis.autonomyPct).toBeGreaterThan(0);
    expect(ov.kpis.reviewQueueCount).toBe(ov.attention.length);
    expect(ov.systemHealth.checkpointStatus).toBe('healthy');
    expect(ov.feedPreview.length).toBeGreaterThan(0);
    // every attention item carries the decision sub-objects the cards need
    for (const act of ov.attention) {
      expect(act.jury.dimensions.length).toBe(3);
      expect(act.escalation.kind).toBeTruthy();
      expect(act.idempotencyKey).toMatch(/:/);
    }
  });

  it('getReviewQueue filters by type', async () => {
    const all = await a.getReviewQueue(MOCK_TENANT_ID);
    const posts = await a.getReviewQueue(MOCK_TENANT_ID, { type: 'POST' });
    expect(all.length).toBeGreaterThan(posts.length);
    expect(posts.every((x) => x.type === 'POST')).toBe(true);
  });

  it('runs conform to the RunStatus enum (no PARTIAL)', async () => {
    const runs = await a.getRuns(MOCK_TENANT_ID);
    expect(runs.every((r) => ['RUNNING', 'SUCCESS', 'FAILED'].includes(r.status))).toBe(true);
  });
});

describe('MockAdapter — 439 HOLD safety', () => {
  const a = new MockAdapter();

  it('every channel is held + approve-first (nothing auto-fires)', async () => {
    const t = await a.getTenant(MOCK_TENANT_ID);
    expect(t).not.toBeNull();
    expect(t!.autonomy.every((c) => c.held && c.mode === 'APPROVE_FIRST')).toBe(true);
  });

  it('refuses to switch a held channel to AUTO', async () => {
    const res = await a.setAutonomy(MOCK_TENANT_ID, 'GMAIL', 'AUTO', 0.85);
    // backend gate mirrored: request to AUTO is denied while held
    expect(res.mode).toBe('APPROVE_FIRST');
    expect(res.held).toBe(true);
  });
});

describe('MockAdapter — engine state + mutations', () => {
  it('setEngineState flips RUNNING/PAUSED', async () => {
    const a = new MockAdapter();
    expect(await a.setEngineState(MOCK_TENANT_ID, true)).toBe('PAUSED');
    const t = await a.getTenant(MOCK_TENANT_ID);
    expect(t!.engineState).toBe('PAUSED');
    expect(await a.setEngineState(MOCK_TENANT_ID, false)).toBe('RUNNING');
  });

  it('approveAction returns an APPROVED action (resumes engine; never bypasses a gate)', async () => {
    const a = new MockAdapter();
    const approved = await a.approveAction('act_8f2a1', 'nw:outreach:bayside-pg:c8821');
    expect(approved.status).toBe('APPROVED');
  });
});

describe('MockAdapter — SSE emits feed events', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('dispatches feed.event on its timer and stops on close', () => {
    const a = new MockAdapter();
    const events: unknown[] = [];
    let status = '';
    const sub = a.subscribe(MOCK_TENANT_ID, { 'feed.event': (e) => events.push(e) }, (s) => (status = s));
    expect(status).toBe('open');
    vi.advanceTimersByTime(8000);
    expect(events.length).toBe(1);
    sub.close();
    vi.advanceTimersByTime(16000);
    expect(events.length).toBe(1); // no appends after close
    expect(sub.status()).toBe('closed');
  });

  it('the canonical event names are exhaustive (7)', () => {
    expect(SSE_EVENT_NAMES.length).toBe(7);
  });
});
