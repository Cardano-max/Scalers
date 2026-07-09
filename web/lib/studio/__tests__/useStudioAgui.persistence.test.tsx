import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useStudioAgui, deriveStepsFromHistory } from '../useStudioAgui';
import type { ChatTurn } from '@/lib/data/studio-adapter';

/**
 * PERSISTENCE — the Command tab unmounts on navigation, so returning must rebuild
 * the studio from persisted history. Two guarantees:
 *   1. deriveStepsFromHistory turns persisted agent traces back into DONE steps so
 *      the run's progress survives a tab switch.
 *   2. the mount restore loads the conversation + steps INDEPENDENT of the AG-UI
 *      probe — a flaky/slow probe must not blank out a run that is on disk.
 */

const AGUI = 'http://api/studio/agui';
const GQL = 'http://api/graphql';
const SID = 'studio-live-session';

const HISTORY_ROWS = [
  { id: 't1', sessionId: SID, seq: 1, role: 'operator', text: 'Fill May Tuesdays', model: null, createdAt: '2026-06-30T10:00:00Z' },
  { id: 't2', sessionId: SID, seq: 2, role: 'host', text: 'On it.', model: 'anthropic:claude-haiku-4-5', createdAt: '2026-06-30T10:00:01Z' },
  { id: 't3', sessionId: SID, seq: 3, role: 'strategist', text: '[strategist] angle', model: 'm', createdAt: '2026-06-30T10:00:02Z' },
  { id: 't4', sessionId: SID, seq: 4, role: 'draft', text: '[draft] copy A', model: 'm', createdAt: '2026-06-30T10:00:03Z' },
  { id: 't5', sessionId: SID, seq: 5, role: 'critic', text: '[critic] verdict', model: 'm', createdAt: '2026-06-30T10:00:04Z' },
  { id: 't6', sessionId: SID, seq: 6, role: 'jury', text: '[jury] go', model: 'm', createdAt: '2026-06-30T10:00:05Z' },
];

afterEach(() => vi.restoreAllMocks());

describe('deriveStepsFromHistory', () => {
  it('turns persisted agent traces into DONE steps (operator/host excluded)', () => {
    const turns: ChatTurn[] = [
      { id: 'o', role: 'OPERATOR', label: 'You', text: 'hi', at: '2026-06-30T10:00:00Z' },
      { id: 'h', role: 'SYSTEM', label: 'Studio Host', text: 'hi', at: '2026-06-30T10:00:01Z' },
      { id: 's', role: 'STRATEGIST', label: 'Strategist', text: 'x', at: '2026-06-30T10:00:02Z' },
      { id: 'd', role: 'COPYWRITER', label: 'Draft', text: 'x', at: '2026-06-30T10:00:03Z' },
      { id: 'j', role: 'JURY', label: 'Jury', text: 'x', at: '2026-06-30T10:00:04Z' },
    ];
    const steps = deriveStepsFromHistory(turns);
    expect(steps.map((s) => s.label)).toEqual(['Strategist', 'Draft', 'Jury']);
    expect(steps.every((s) => s.status === 'done')).toBe(true);
    expect(steps[0].id).toBe('hist_s');
  });
});

function mockFetch(probeOk: boolean) {
  return vi.fn(async (url: string) => {
    if (url === GQL) {
      return {
        ok: true,
        json: async () => ({ data: { studioChatHistory: HISTORY_ROWS } }),
      } as unknown as Response;
    }
    // AG-UI probe
    return { ok: probeOk, body: { cancel: async () => {} } } as unknown as Response;
  });
}

describe('useStudioAgui — mount restore', () => {
  it('restores the conversation + run steps even when the probe FAILS', async () => {
    vi.stubGlobal('fetch', mockFetch(false));
    const { result } = renderHook(() => useStudioAgui(AGUI, GQL, SID));

    await waitFor(() => expect(result.current.turns.length).toBe(6));
    // run state survives: the 4 agent traces became done steps.
    expect(result.current.steps.length).toBe(4);
    expect(result.current.steps.every((s) => s.status === 'done')).toBe(true);
    // probe failed -> honest preview banner, but the thread is NOT blank.
    expect(result.current.connected).toBe(false);
    expect(result.current.streamStatus).toBe('preview');
  });

  it('restores and reports connected when the probe succeeds', async () => {
    vi.stubGlobal('fetch', mockFetch(true));
    const { result } = renderHook(() => useStudioAgui(AGUI, GQL, SID));

    await waitFor(() => expect(result.current.connected).toBe(true));
    expect(result.current.turns.length).toBe(6);
    expect(result.current.steps.length).toBe(4);
    expect(result.current.streamStatus).toBe('open');
  });
});
