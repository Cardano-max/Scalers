import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useStudioAgui } from '../useStudioAgui';

/**
 * UNIFICATION — voice and text are ONE conversation on ONE session, not two windows.
 *
 * The operator asked to stop having a separate "chat convo" and "voice convo". This
 * proves the contract that makes them one:
 *   - a SPOKEN turn (recorded by the voice host via recordVoiceTurn) and a TYPED turn
 *     (sent via send) both land in the SAME transcript (studio.turns), interleaved;
 *   - the typed turn is POSTed with the SAME session id (threadId) the voice WebRTC
 *     session mints with — so both halves of the conversation share one session.
 */

const AGUI = 'http://api/studio/agui';
const GQL = 'http://api/graphql';
const SID = 'studio-live-session';

afterEach(() => vi.restoreAllMocks());

/** A minimal SSE Response whose body yields the given frames once, then closes. */
function sseResponse(frames: string[]): Response {
  const encoder = new TextEncoder();
  let i = 0;
  return {
    ok: true,
    body: {
      getReader() {
        return {
          read: async () =>
            i < frames.length
              ? { done: false, value: encoder.encode(frames[i++]) }
              : { done: true, value: undefined },
          cancel: async () => {},
        };
      },
      cancel: async () => {},
    },
  } as unknown as Response;
}

const HOST_REPLY =
  `data: ${JSON.stringify({ type: 'TEXT_MESSAGE_CONTENT', delta: 'Got it — typed brief received.' })}\n\n` +
  `data: ${JSON.stringify({ type: 'RUN_FINISHED', outcome: { type: 'success' } })}\n\n`;

describe('useStudioAgui — voice + text are one session/conversation', () => {
  it('a typed message and a spoken turn land in the same transcript on the same session id', async () => {
    let sentThreadId: string | null = null;
    let typedPersisted = false;

    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === GQL) {
        // Mount restore is empty; once the typed turn is sent the backend persists it,
        // so the post-run history refresh returns it (mirrors the real round-trip).
        const rows = typedPersisted
          ? [
              { id: 'u1', sessionId: SID, seq: 1, role: 'operator', text: 'typed brief', model: null, createdAt: '2026-06-30T10:00:10Z' },
              { id: 'h1', sessionId: SID, seq: 2, role: 'host', text: 'Got it — typed brief received.', model: 'm', createdAt: '2026-06-30T10:00:11Z' },
            ]
          : [];
        return { ok: true, json: async () => ({ data: { studioChatHistory: rows } }) } as unknown as Response;
      }
      // AG-UI endpoint: the cheap reachability probe uses a 'probe-…' threadId; a real
      // turn uses the session id. Only the latter is a sent conversation message.
      const body = JSON.parse(String(init?.body ?? '{}'));
      const threadId = String(body.threadId ?? '');
      if (threadId.startsWith('probe-')) {
        return { ok: true, body: { cancel: async () => {} } } as unknown as Response;
      }
      sentThreadId = threadId;
      typedPersisted = true;
      return sseResponse([HOST_REPLY]);
    });
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useStudioAgui(AGUI, GQL, SID));

    // Reachable after the probe.
    await waitFor(() => expect(result.current.connected).toBe(true));

    // The voice host records a finalized SPOKEN turn into the shared transcript…
    act(() => {
      result.current.recordVoiceTurn('OPERATOR', 'You', 'spoken brief');
    });
    // …and the operator also TYPES — same session, continuing the SAME conversation.
    act(() => {
      result.current.send('typed brief');
    });

    // Both the spoken and the typed line appear in the ONE merged transcript.
    await waitFor(() => {
      const texts = result.current.turns.map((t) => t.text);
      expect(texts).toContain('spoken brief');
      expect(texts).toContain('typed brief');
    });

    // The typed turn went to the SAME session id the voice session mints with.
    await waitFor(() => expect(sentThreadId).toBe(SID));
  });

  it('a recorded voice turn survives a history refresh (it is not wiped by re-fetch)', async () => {
    // History always returns one persisted operator line; the probe succeeds.
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === GQL) {
        return {
          ok: true,
          json: async () => ({
            data: {
              studioChatHistory: [
                { id: 'u1', sessionId: SID, seq: 1, role: 'operator', text: 'persisted line', model: null, createdAt: '2026-06-30T10:00:00Z' },
              ],
            },
          }),
        } as unknown as Response;
      }
      const body = JSON.parse(String(init?.body ?? '{}'));
      if (String(body.threadId ?? '').startsWith('probe-')) {
        return { ok: true, body: { cancel: async () => {} } } as unknown as Response;
      }
      return sseResponse([HOST_REPLY]);
    });
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useStudioAgui(AGUI, GQL, SID));
    await waitFor(() => expect(result.current.turns.map((t) => t.text)).toContain('persisted line'));

    act(() => result.current.recordVoiceTurn('SYSTEM', 'Studio Host', 'spoken host line'));
    // Force a history refresh (send → refreshHistory replaces the persisted turns).
    act(() => result.current.send('hello'));

    await waitFor(() => {
      const texts = result.current.turns.map((t) => t.text);
      // The persisted line is still there AND the spoken line was not clobbered.
      expect(texts).toContain('persisted line');
      expect(texts).toContain('spoken host line');
    });
  });
});
