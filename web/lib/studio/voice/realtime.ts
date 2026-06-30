/**
 * realtime.ts — browser client for the speech-to-speech voice layer (OpenAI
 * Realtime, option B) over WebRTC.
 *
 * Posture (mirrors engine/studio/voice.py):
 *  - The raw OPENAI_API_KEY NEVER reaches the browser. We mint a short-TTL ephemeral
 *    client secret from our own server (`POST /studio/voice/session`) and use only
 *    that `ek_...` value for the WebRTC SDP exchange with OpenAI.
 *  - The realtime model is given EXACTLY TWO tools (update_plan +
 *    request_orchestration) by the server-minted session. This relay only routes
 *    those two names — there is NO publish/send path in the browser either.
 *  - Tool calls are handled on the SERVER (the GO-gate is server-side). The browser
 *    forwards the model's tool-call arguments to our routes and feeds the JSON result
 *    back as a `function_call_output`. The browser never decides to launch.
 *
 * The pure pieces (mint + tool routing) are exported separately so they can be
 * unit-tested without a real RTCPeerConnection.
 */

/** Derive the `/studio` base from the configured AG-UI URL (strip trailing /agui). */
export function studioBase(aguiUrl: string): string {
  return aguiUrl.replace(/\/agui(\?.*)?$/, '');
}

export interface MintedVoiceSession {
  value: string; // ephemeral ek_... secret (NOT the raw key)
  model: string;
  callUrl: string;
  tools: string[];
  expiresAt?: number | null;
}

/** Mint an ephemeral Realtime client secret from OUR server (raw key stays server-side). */
export async function mintVoiceSession(
  aguiUrl: string,
  sessionId: string,
  signal?: AbortSignal,
): Promise<MintedVoiceSession> {
  const res = await fetch(`${studioBase(aguiUrl)}/voice/session`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ sessionId }),
    signal,
  });
  if (!res.ok) throw new Error(`voice session mint HTTP ${res.status}`);
  const d = (await res.json()) as {
    ok?: boolean;
    value?: string;
    model?: string;
    callUrl?: string;
    tools?: string[];
    expiresAt?: number | null;
    error?: string;
  };
  if (!d.ok || !d.value) throw new Error(d.error ?? 'voice session mint failed');
  return {
    value: d.value,
    model: d.model ?? 'gpt-realtime',
    callUrl: d.callUrl ?? 'https://api.openai.com/v1/realtime/calls',
    tools: d.tools ?? [],
    expiresAt: d.expiresAt ?? null,
  };
}

export interface PlanUpdateResult {
  ok: boolean;
  plan?: Record<string, unknown>;
  awaitingGo?: boolean;
  runnable?: boolean;
  readback?: string;
}

export interface OrchestrateResult {
  ok: boolean;
  launched: boolean;
  runId?: string;
  campaignId?: string;
  status?: string;
  gate?: { launch: boolean; armed: boolean; classification: string; reason: string };
}

/** Forward an `update_plan` tool call to the server (persists via _persist_plan). */
export async function postPlanUpdate(
  aguiUrl: string,
  sessionId: string,
  fields: Record<string, unknown>,
): Promise<PlanUpdateResult> {
  const res = await fetch(`${studioBase(aguiUrl)}/voice/plan`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ sessionId, fields }),
  });
  if (!res.ok) throw new Error(`voice plan HTTP ${res.status}`);
  return (await res.json()) as PlanUpdateResult;
}

/** Forward a `request_orchestration` tool call + the latest spoken transcript to the
 *  SERVER-SIDE 2-factor GO-gate. The server (not the browser) decides to launch. */
export async function postOrchestrate(
  aguiUrl: string,
  sessionId: string,
  transcript: string,
): Promise<OrchestrateResult> {
  const res = await fetch(`${studioBase(aguiUrl)}/voice/orchestrate`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ sessionId, transcript }),
  });
  if (!res.ok) throw new Error(`voice orchestrate HTTP ${res.status}`);
  return (await res.json()) as OrchestrateResult;
}

/** The ONLY two tool names this relay will route. Anything else is dropped with an
 *  honest error output — there is no send/publish handler in the browser. */
export const ROUTABLE_TOOLS = ['update_plan', 'request_orchestration'] as const;
export type RoutableTool = (typeof ROUTABLE_TOOLS)[number];

export interface ToolRouteDeps {
  aguiUrl: string;
  sessionId: string;
  /** The latest finalized user-speech transcript (for the GO-gate go-phrase factor). */
  latestTranscript: () => string;
  onPlan?: (r: PlanUpdateResult) => void;
  onOrchestrate?: (r: OrchestrateResult) => void;
}

/**
 * Route ONE model tool call to its server handler and return the JSON the browser
 * must feed back as a `function_call_output`. Pure w.r.t. WebRTC (only does fetch),
 * so it is unit-testable. NEVER sends/publishes — only the two routes exist.
 */
export async function routeToolCall(
  name: string,
  rawArgs: string,
  deps: ToolRouteDeps,
): Promise<Record<string, unknown>> {
  let args: Record<string, unknown> = {};
  try {
    args = rawArgs ? (JSON.parse(rawArgs) as Record<string, unknown>) : {};
  } catch {
    args = {};
  }

  if (name === 'update_plan') {
    const r = await postPlanUpdate(deps.aguiUrl, deps.sessionId, args);
    deps.onPlan?.(r);
    return {
      ok: r.ok,
      readback: r.readback ?? '',
      awaiting_go: !!r.awaitingGo,
      note: 'Plan updated and persisted (HELD). Nothing was sent.',
    };
  }

  if (name === 'request_orchestration') {
    const r = await postOrchestrate(deps.aguiUrl, deps.sessionId, deps.latestTranscript());
    deps.onOrchestrate?.(r);
    if (r.launched) {
      return {
        ok: true,
        launched: true,
        run_id: r.runId,
        note:
          'Held multi-agent run launched. Narrate each agent result as it lands; ' +
          'everything is HELD for approval and nothing was sent.',
      };
    }
    return {
      ok: true,
      launched: false,
      reason: r.gate?.reason ?? 'refused',
      note:
        'The server GO-gate refused (not a launch). Treat this as an edit / keep ' +
        'interviewing — do not claim the campaign ran.',
    };
  }

  // No other tool is routable — there is deliberately no send/publish handler.
  return {
    ok: false,
    error: `tool ${name} is not available to the voice agent`,
  };
}
