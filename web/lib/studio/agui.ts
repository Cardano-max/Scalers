/**
 * agui.ts — a real, dependency-free AG-UI protocol client for the Campaign Studio.
 *
 * The studio backend (engine/studio/agui.py) exposes the pydantic-ai `AGUIAdapter`
 * at `POST /studio/agui`: it accepts an AG-UI `RunAgentInput` (JSON) and streams
 * AG-UI events back as Server-Sent Events (`data: {json}\n\n`, camelCase, omitting
 * nulls). This module speaks that wire protocol directly — no CopilotKit runtime
 * and no `@ag-ui/client` are installed (the console's node_modules is shared and
 * frozen), so we parse the SSE stream ourselves rather than fabricate any of it.
 *
 * HONESTY: every turn here is a real round-trip to the backend. There is no canned
 * reply path. If the fetch fails or the backend is unreachable, the caller falls
 * back to the honest preview state — it never synthesizes an agent turn.
 *
 * Wire facts (verified against ag-ui-protocol 0.1.19 / pydantic-ai 2.0.0):
 *  - `revise_plan` (a tool) returns a `StateSnapshotEvent`, so the SSE stream
 *    carries a `STATE_SNAPSHOT` whose `snapshot` is the full CampaignPlan —
 *    that is the bidirectional shared-state sync.
 *  - an approval-gated tool (`stage_publish`, `requires_approval=True`) does NOT
 *    run its body; the run ends with `RUN_FINISHED.outcome.type === 'interrupt'`
 *    carrying `interrupts[]`. The client resumes by re-POSTing the same messages
 *    plus the proposed assistant tool-call message and a `resume[]` entry.
 */

// --- shared-state plan -------------------------------------------------------

/** The AG-UI shared state mirrored from engine/studio/agui.py `CampaignPlan`. */
export interface CampaignPlan {
  goal: string;
  audience: string;
  channels: string[];
  sections: string[];
  tasks_per_role: Record<string, string[]>;
  assets: Array<Record<string, unknown>>;
  schedule: Record<string, string>;
  // Operator brand/strategy notes (uploaded). Free text, persisted with the plan.
  notes?: string;
  // Interview-gathered run parameters (Agency-page scoping gate). Optional because a
  // plan starts empty and the supervisor interview fills them in before a run arms.
  output_count?: number;
  action_type?: string;
  lead_count?: number;
  tone?: string;
  campaign_type?: string;
  deep_research?: boolean | null;
  drafts_only?: boolean | null;
  // Uploaded customer list — a real parse of the operator's CSV, surfaced to the
  // supervisor so it can truthfully read the rows. Empty/absent = no CSV uploaded.
  customers?: {
    filename?: string;
    rows?: number;
    columns?: string[];
    sample?: Array<Record<string, string>>;
    ingested?: boolean;
  };
}

export function emptyPlan(): CampaignPlan {
  return {
    goal: '',
    audience: '',
    channels: [],
    sections: [],
    tasks_per_role: {},
    assets: [],
    schedule: {},
  };
}

// --- AG-UI message + run-input shapes ---------------------------------------

export interface AguiFunctionCall {
  name: string;
  arguments: string;
}
export interface AguiToolCall {
  id: string;
  type: 'function';
  function: AguiFunctionCall;
}
export interface AguiMessage {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content?: string;
  toolCalls?: AguiToolCall[];
  toolCallId?: string;
}
export interface AguiResumeEntry {
  interruptId: string;
  status: 'resolved' | 'cancelled';
  payload?: unknown;
}
export interface RunAgentInput {
  threadId: string;
  runId: string;
  state: unknown;
  messages: AguiMessage[];
  tools: unknown[];
  context: unknown[];
  forwardedProps: unknown;
  resume?: AguiResumeEntry[];
}

// --- the proposed-but-unapproved call surfaced by the gate ------------------

export interface AguiInterrupt {
  id: string;
  reason: string;
  message?: string;
  toolCallId?: string;
  responseSchema?: unknown;
}

/** A single observed tool call (name + accumulated raw JSON args). */
export interface ObservedToolCall {
  id: string;
  name: string;
  args: string;
}

/** Structured result of one streamed run. */
export interface AguiRunResult {
  /** The host's final assistant text for this run (may be empty). */
  hostText: string;
  /** Tool calls the model issued this run, in order. */
  toolCalls: ObservedToolCall[];
  /** Latest STATE_SNAPSHOT plan, if the agent revised the plan this run. */
  state?: CampaignPlan;
  /** Non-empty when the run paused on an approval gate. */
  interrupts: AguiInterrupt[];
  /** Set when the backend emitted RUN_ERROR (real backend error, surfaced honestly). */
  error?: string;
}

export interface AguiRunHandlers {
  /** Live host text delta as it streams (for the in-flight host turn). */
  onHostDelta?: (text: string) => void;
  /** A tool call started (name known). */
  onToolCall?: (call: ObservedToolCall) => void;
  /** A STATE_SNAPSHOT arrived — the new shared-state plan. */
  onState?: (plan: CampaignPlan) => void;
}

function genId(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * Stream one AG-UI run. POSTs `input` to `url` and parses the SSE event stream,
 * invoking `handlers` live and returning the structured `AguiRunResult` when the
 * run finishes. Throws on transport failure (caller degrades to honest preview).
 */
export async function runAgui(
  url: string,
  input: RunAgentInput,
  handlers: AguiRunHandlers = {},
  signal?: AbortSignal,
): Promise<AguiRunResult> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
    body: JSON.stringify(input),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`studio AG-UI HTTP ${res.status}`);
  }

  const result: AguiRunResult = { hostText: '', toolCalls: [], interrupts: [] };
  const argsById = new Map<string, ObservedToolCall>();

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const handleFrame = (jsonStr: string) => {
    let ev: Record<string, unknown>;
    try {
      ev = JSON.parse(jsonStr);
    } catch {
      return;
    }
    const type = ev.type as string;
    switch (type) {
      case 'TEXT_MESSAGE_CONTENT': {
        const delta = (ev.delta as string) ?? '';
        result.hostText += delta;
        handlers.onHostDelta?.(delta);
        break;
      }
      case 'TOOL_CALL_START': {
        const call: ObservedToolCall = {
          id: ev.toolCallId as string,
          name: ev.toolCallName as string,
          args: '',
        };
        argsById.set(call.id, call);
        result.toolCalls.push(call);
        handlers.onToolCall?.(call);
        break;
      }
      case 'TOOL_CALL_ARGS': {
        const call = argsById.get(ev.toolCallId as string);
        if (call) call.args += (ev.delta as string) ?? '';
        break;
      }
      case 'STATE_SNAPSHOT': {
        const plan = ev.snapshot as CampaignPlan;
        result.state = plan;
        handlers.onState?.(plan);
        break;
      }
      case 'RUN_FINISHED': {
        const outcome = ev.outcome as { type?: string; interrupts?: AguiInterrupt[] } | undefined;
        if (outcome?.type === 'interrupt' && outcome.interrupts) {
          result.interrupts = outcome.interrupts;
        }
        break;
      }
      case 'RUN_ERROR': {
        result.error = (ev.message as string) ?? 'studio run error';
        break;
      }
      default:
        break;
    }
  };

  // SSE framing: events separated by a blank line; each `data:` line is JSON.
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const rawEvent = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      for (const line of rawEvent.split('\n')) {
        const trimmed = line.replace(/\r$/, '');
        if (trimmed.startsWith('data:')) handleFrame(trimmed.slice(5).trim());
      }
    }
  }
  return result;
}

/** Build the proposed assistant tool-call message for an approval resume. */
export function assistantToolCallMessage(
  call: ObservedToolCall,
  precedingText: string,
): AguiMessage {
  return {
    id: genId('a'),
    role: 'assistant',
    content: precedingText || undefined,
    toolCalls: [
      { id: call.id, type: 'function', function: { name: call.name, arguments: call.args || '{}' } },
    ],
  };
}

export function userMessage(text: string): AguiMessage {
  return { id: genId('u'), role: 'user', content: text };
}

export function newRunInput(
  threadId: string,
  messages: AguiMessage[],
  state: CampaignPlan,
  resume?: AguiResumeEntry[],
): RunAgentInput {
  return {
    threadId,
    runId: genId('run'),
    state,
    messages,
    tools: [],
    context: [],
    forwardedProps: {},
    ...(resume ? { resume } : {}),
  };
}

/** Cheap reachability probe: an empty run input. Reachable resolves true. */
export async function probeAgui(url: string, signal?: AbortSignal): Promise<boolean> {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
      body: JSON.stringify(
        newRunInput(`probe-${Date.now()}`, [], emptyPlan()),
      ),
      signal,
    });
    // Any HTTP response (even a streamed run) means the endpoint is live. We do not
    // need to consume it; cancel the body so the run is abandoned server-side.
    res.body?.cancel().catch(() => {});
    return res.ok;
  } catch {
    return false;
  }
}
