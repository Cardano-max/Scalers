import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  runAgui,
  newRunInput,
  userMessage,
  assistantToolCallMessage,
  emptyPlan,
  type ObservedToolCall,
} from '../agui';

/**
 * Hermetic protocol tests for the AG-UI client. No network: we stub `fetch` with a
 * ReadableStream of the EXACT SSE frames the backend emits (verified against
 * ag-ui-protocol 0.1.19 / pydantic-ai 2.0.0). This proves the client decodes a real
 * stream — host text, the STATE_SNAPSHOT shared-state sync, and the approval-gate
 * interrupt — rather than fabricating any of it.
 */

function sseStream(frames: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const enc = new TextEncoder();
      for (const f of frames) controller.enqueue(enc.encode(`data: ${f}\n\n`));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('runAgui — AG-UI SSE protocol mapping', () => {
  it('streams host text deltas and finishes with success', async () => {
    const frames = [
      JSON.stringify({ type: 'RUN_STARTED', threadId: 't', runId: 'r' }),
      JSON.stringify({ type: 'TEXT_MESSAGE_START', messageId: 'm1', role: 'assistant' }),
      JSON.stringify({ type: 'TEXT_MESSAGE_CONTENT', messageId: 'm1', delta: 'Hello ' }),
      JSON.stringify({ type: 'TEXT_MESSAGE_CONTENT', messageId: 'm1', delta: 'there.' }),
      JSON.stringify({ type: 'TEXT_MESSAGE_END', messageId: 'm1' }),
      JSON.stringify({ type: 'RUN_FINISHED', threadId: 't', runId: 'r', outcome: { type: 'success' } }),
    ];
    const deltas: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async () => sseStream(frames)));
    const result = await runAgui(
      '/studio/agui',
      newRunInput('t', [userMessage('hi')], emptyPlan()),
      { onHostDelta: (d) => deltas.push(d) },
    );
    expect(result.hostText).toBe('Hello there.');
    expect(deltas).toEqual(['Hello ', 'there.']);
    expect(result.interrupts).toHaveLength(0);
    expect(result.error).toBeUndefined();
  });

  it('captures a STATE_SNAPSHOT as the bidirectional shared-state sync', async () => {
    const snapshot = {
      goal: 'fill May Tuesdays',
      audience: 'local fine-line fans',
      channels: ['instagram', 'email'],
      sections: [],
      tasks_per_role: {},
      assets: [],
      schedule: {},
    };
    const frames = [
      JSON.stringify({ type: 'RUN_STARTED', threadId: 't', runId: 'r' }),
      JSON.stringify({ type: 'TOOL_CALL_START', toolCallId: 'tc1', toolCallName: 'revise_plan' }),
      JSON.stringify({ type: 'TOOL_CALL_ARGS', toolCallId: 'tc1', delta: '{"goal":"x"}' }),
      JSON.stringify({ type: 'TOOL_CALL_END', toolCallId: 'tc1' }),
      JSON.stringify({ type: 'STATE_SNAPSHOT', snapshot }),
      JSON.stringify({ type: 'RUN_FINISHED', threadId: 't', runId: 'r', outcome: { type: 'success' } }),
    ];
    let synced: unknown = null;
    vi.stubGlobal('fetch', vi.fn(async () => sseStream(frames)));
    const result = await runAgui(
      '/studio/agui',
      newRunInput('t', [userMessage('set goal')], emptyPlan()),
      { onState: (p) => (synced = p) },
    );
    expect(result.state?.goal).toBe('fill May Tuesdays');
    expect(result.state?.channels).toEqual(['instagram', 'email']);
    expect(synced).toEqual(snapshot);
    expect(result.toolCalls[0]).toMatchObject({ name: 'revise_plan', args: '{"goal":"x"}' });
  });

  it('surfaces an approval-gate interrupt without running the tool body', async () => {
    const frames = [
      JSON.stringify({ type: 'RUN_STARTED', threadId: 't', runId: 'r' }),
      JSON.stringify({ type: 'TOOL_CALL_START', toolCallId: 'tc9', toolCallName: 'stage_publish' }),
      JSON.stringify({ type: 'TOOL_CALL_ARGS', toolCallId: 'tc9', delta: '{"channel":"instagram","draft":"x"}' }),
      JSON.stringify({ type: 'TOOL_CALL_END', toolCallId: 'tc9' }),
      JSON.stringify({
        type: 'RUN_FINISHED',
        threadId: 't',
        runId: 'r',
        outcome: {
          type: 'interrupt',
          interrupts: [
            {
              id: 'int-tc9',
              reason: 'tool_call',
              toolCallId: 'tc9',
              message: 'Approve stage_publish(...)?',
            },
          ],
        },
      }),
    ];
    vi.stubGlobal('fetch', vi.fn(async () => sseStream(frames)));
    const result = await runAgui('/studio/agui', newRunInput('t', [userMessage('post it')], emptyPlan()));
    expect(result.interrupts).toHaveLength(1);
    expect(result.interrupts[0].id).toBe('int-tc9');
    expect(result.interrupts[0].toolCallId).toBe('tc9');
    // No TOOL_CALL_RESULT was emitted -> the body did not run (held for approval).
    const call = result.toolCalls.find((c) => c.id === 'tc9') as ObservedToolCall;
    const resume = assistantToolCallMessage(call, result.hostText);
    expect(resume.toolCalls?.[0].function.name).toBe('stage_publish');
    expect(resume.toolCalls?.[0].id).toBe('tc9');
  });

  it('reports a RUN_ERROR honestly instead of fabricating a reply', async () => {
    const frames = [
      JSON.stringify({ type: 'RUN_STARTED', threadId: 't', runId: 'r' }),
      JSON.stringify({ type: 'RUN_ERROR', message: 'model exploded' }),
    ];
    vi.stubGlobal('fetch', vi.fn(async () => sseStream(frames)));
    const result = await runAgui('/studio/agui', newRunInput('t', [userMessage('hi')], emptyPlan()));
    expect(result.error).toBe('model exploded');
    expect(result.hostText).toBe('');
  });

  it('throws on a non-OK transport (caller degrades to honest preview)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('nope', { status: 502 })));
    await expect(
      runAgui('/studio/agui', newRunInput('t', [userMessage('hi')], emptyPlan())),
    ).rejects.toThrow(/HTTP 502/);
  });
});
