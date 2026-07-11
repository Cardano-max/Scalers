/**
 * run-trace — the competitor-pick seam (the mirror of select-artwork).
 *
 * Pins: selectCompetitor POSTs the REAL optionId to
 * /studio/campaign/{runId}/select-competitor; fetchRunState parses a
 * competitor_selection_request defensively (snake or camel case, options without a
 * real id dropped, non-numeric metrics dropped) and reads HONEST-EMPTY (null) when
 * the engine sent none — never a fabricated competitor.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchRunState, selectCompetitor } from '../run-trace';

const AGUI = 'http://engine.test/studio/agui';

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('selectCompetitor', () => {
  it('POSTs the optionId to /studio/campaign/{runId}/select-competitor', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);
    await selectCompetitor(AGUI, 'run_9', 'opt_2');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('http://engine.test/studio/campaign/run_9/select-competitor');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ optionId: 'opt_2' });
  });

  it('throws on a backend refusal (honest failure, no silent resume)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ ok: false, error: 'no such option' })));
    await expect(selectCompetitor(AGUI, 'run_9', 'opt_x')).rejects.toThrow('no such option');
  });
});

describe('fetchRunState — competitorSelectionRequest parse', () => {
  it('parses the engine competitor_selection_request (snake case) defensively', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          runId: 'run_9',
          status: 'awaiting_selection',
          steps: [],
          competitor_selection_request: {
            kind: 'competitor_pick',
            question: 'Which competitor post should we mold?',
            options: [
              {
                id: 'opt_1',
                handle: 'inkrivals',
                caption: 'Fresh fine-line sleeve',
                url: 'https://instagram.com/p/abc',
                metrics: { likes: 4210, comments: 187, bogus: 'NaN-ish' },
                total_score: 92.5,
                why_it_worked: 'Healed proof reads as trust.',
                visual_tags: ['fine-line', 7],
              },
              { handle: 'no-id-so-dropped' },
            ],
          },
        }),
      ),
    );
    const st = await fetchRunState(AGUI, 'run_9');
    expect(st.status).toBe('awaiting_selection');
    const req = st.competitorSelectionRequest;
    expect(req).not.toBeNull();
    expect(req?.options).toHaveLength(1); // the id-less option is dropped
    const opt = req!.options[0];
    expect(opt.id).toBe('opt_1');
    expect(opt.handle).toBe('inkrivals');
    expect(opt.metrics).toEqual({ likes: 4210, comments: 187 }); // non-numeric dropped
    expect(opt.totalScore).toBe(92.5);
    expect(opt.whyItWorked).toBe('Healed proof reads as trust.');
    expect(opt.visualTags).toEqual(['fine-line']); // non-string tag dropped
  });

  it('reads honest-empty (null) when the engine sent no competitor request', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ runId: 'run_9', status: 'running', steps: [] })),
    );
    const st = await fetchRunState(AGUI, 'run_9');
    expect(st.competitorSelectionRequest).toBeNull();
  });

  it('drops a request whose options are all unusable (no fabricated pick)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          runId: 'run_9',
          status: 'awaiting_selection',
          steps: [],
          competitorSelectionRequest: { kind: 'competitor_pick', question: 'q', options: [{}] },
        }),
      ),
    );
    const st = await fetchRunState(AGUI, 'run_9');
    expect(st.competitorSelectionRequest).toBeNull();
  });
});
