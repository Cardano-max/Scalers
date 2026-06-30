/**
 * StudioAdapter — the typed seam for the interactive Campaign Studio (Command tab).
 *
 * This is SCAFFOLDING (P2). The method signatures here are the contract the real
 * P1/P2 backend (engine/studio/orchestration.py + obsapi mutations + SSE, see
 * docs/adr/command-campaign-studio.md Decision 6/7/10) will implement later. The
 * frontend binds to THIS interface only, so flipping from the preview stub to a
 * live adapter swaps the backend without touching a single component.
 *
 * HONESTY GATE: the only implementation shipped today is `PreviewStudioAdapter`,
 * which is explicitly NOT connected to live agents. It never fabricates an agent
 * conversation: `streamAgentTurns` emits nothing, the mutating seams reject
 * loudly, and `getPlanDoc` returns a clearly-labeled empty preview scaffold.
 * `sendChatMessage` only ever echoes the operator's OWN typed message back as
 * their own turn — no canned "agent is typing" script, no fake replies.
 */

// --- roles & display ---------------------------------------------------------

/** Who authored a chat turn / owns a live-progress step. Operator + the named
 *  multi-agent team from the ADR (Researcher, Strategist, Copywriter, Critic,
 *  Jury, Safety) plus a SYSTEM channel for studio/preview notices. */
export type StudioRole =
  | 'OPERATOR'
  | 'RESEARCHER'
  | 'STRATEGIST'
  | 'COPYWRITER'
  | 'CRITIC'
  | 'JURY'
  | 'SAFETY'
  | 'SYSTEM';

/** Human-facing label per role (handoff "role label"). */
export const STUDIO_ROLE_LABEL: Record<StudioRole, string> = {
  OPERATOR: 'You',
  RESEARCHER: 'Researcher',
  STRATEGIST: 'Strategist',
  COPYWRITER: 'Copywriter',
  CRITIC: 'Critic',
  JURY: 'Jury',
  SAFETY: 'Safety',
  SYSTEM: 'Studio',
};

/** Accent color per role (mirrors lib/tokens worker palette; teal = operator). */
export const STUDIO_ROLE_COLOR: Record<StudioRole, string> = {
  OPERATOR: '#0F8A82',
  RESEARCHER: '#2563C9',
  STRATEGIST: '#7A5AF8',
  COPYWRITER: '#9A6B00',
  CRITIC: '#B42318',
  JURY: '#0B6F68',
  SAFETY: '#B42318',
  SYSTEM: '#8C877D',
};

// --- chat ------------------------------------------------------------------

/**
 * One message in the studio conversation. Designed for STREAMING: a turn can be
 * appended empty with `streaming: true` and grown incrementally via the stream's
 * `onTurnDelta` before being finalized — the chat panel renders partial text as
 * it arrives. `at` is an ISO-8601 timestamp.
 */
export interface ChatTurn {
  id: string;
  role: StudioRole;
  /** Display label; defaults to STUDIO_ROLE_LABEL[role] when omitted by backend. */
  label: string;
  text: string;
  at: string;
  /** True while incremental deltas are still arriving for this turn. */
  streaming?: boolean;
}

// --- live progress ---------------------------------------------------------

export type AgentStepStatus =
  | 'pending'
  | 'running'
  | 'done'
  | 'failed'
  | 'blocked';

/**
 * One unit of work in the live-progress area (e.g. "Research", "Draft", "Jury").
 * `status` drives the indicator; `detail` is an optional one-line note. A
 * `blocked` step carries the honest reason a capability is gated (e.g. "Firecrawl
 * pending operator setup", "brand-voice skill pending security review").
 */
export interface AgentStep {
  id: string;
  agent: StudioRole;
  label: string;
  status: AgentStepStatus;
  detail?: string;
}

// --- plan / spec doc -------------------------------------------------------

export type PlanDocStatus = 'draft' | 'approved' | 'executing' | 'executed';

/**
 * The editable campaign plan/spec document (ADR Decision 5b — the living spec,
 * not a chat transcript). `version` is the monotonic save counter shown in the
 * version label; `body` is the editable text/markdown surface.
 */
export interface PlanDoc {
  id: string;
  sessionId: string;
  version: number;
  title: string;
  body: string;
  status: PlanDocStatus;
  updatedAt: string;
}

// --- streaming -------------------------------------------------------------

export type StudioStreamStatus =
  | 'preview'
  | 'connecting'
  | 'open'
  | 'closed'
  | 'error';

/** Callbacks the chat + live-progress panels register to receive incremental
 *  agent output. The real backend drives these from the SSE
 *  `campaign.step.completed` stream (ADR Decision 6). */
export interface StudioStreamHandlers {
  /** A new (possibly streaming) agent turn started. */
  onTurn?: (turn: ChatTurn) => void;
  /** Incremental text appended to an in-flight streaming turn. */
  onTurnDelta?: (turnId: string, textDelta: string) => void;
  /** A live-progress step was created or transitioned. */
  onStep?: (step: AgentStep) => void;
  /** Stream lifecycle / honesty signal ('preview' = not wired to live agents). */
  onStatus?: (status: StudioStreamStatus) => void;
}

/** Handle returned by `streamAgentTurns`; `close()` tears the subscription down. */
export interface StudioStream {
  close(): void;
  readonly status: StudioStreamStatus;
}

// --- the seam --------------------------------------------------------------

export interface StudioAdapter {
  /** Human-facing label for the active source — shown in the UI banner. */
  readonly source: 'preview' | 'live';

  /**
   * Persist the operator's message and return it as a ChatTurn. The agent
   * replies (if any) arrive asynchronously via `streamAgentTurns`, never as the
   * return of this call.
   */
  sendChatMessage(sessionId: string, text: string): Promise<ChatTurn>;

  /**
   * Subscribe to streamed agent turns + live-progress steps for a session.
   * Returns immediately with a handle; turns/steps arrive via the handlers.
   */
  streamAgentTurns(
    sessionId: string,
    handlers: StudioStreamHandlers,
  ): StudioStream;

  /** Load the current plan/spec document for a session (null if none yet). */
  getPlanDoc(sessionId: string): Promise<PlanDoc | null>;

  /** Persist an edited plan body; returns the new version. */
  savePlanDoc(sessionId: string, body: string): Promise<PlanDoc>;

  /**
   * Lock the plan and transition the session to the execute phase (ADR Decision
   * 10 — nothing runs until this is called). Returns the approved doc.
   */
  approvePlan(sessionId: string): Promise<PlanDoc>;
}

// --- preview (not-wired) implementation ------------------------------------

/** Thrown by the mutating seams in preview so a mis-wire fails loudly instead of
 *  faking success. */
export class StudioNotWiredError extends Error {
  constructor(method: string) {
    super(
      `StudioAdapter (preview): ${method} is not wired to a live backend yet. ` +
        'This is P2 scaffolding — the real studio orchestration ships in P1/P2 backend.',
    );
    this.name = 'StudioNotWiredError';
  }
}

/**
 * The honest preview adapter. Renders structure, fabricates nothing:
 *  - sendChatMessage → echoes the operator's OWN message as their turn.
 *  - streamAgentTurns → reports status 'preview' and emits NO agent turns/steps.
 *  - getPlanDoc → a clearly-labeled empty preview scaffold (version 0).
 *  - savePlanDoc / approvePlan → reject (not wired) so nothing pretends to persist.
 */
export class PreviewStudioAdapter implements StudioAdapter {
  readonly source = 'preview' as const;

  async sendChatMessage(_sessionId: string, text: string): Promise<ChatTurn> {
    // Echo the operator's own input back as their turn. No agent reply is
    // fabricated — agent turns would arrive via streamAgentTurns, which emits
    // nothing in preview.
    return {
      id: `op_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      role: 'OPERATOR',
      label: STUDIO_ROLE_LABEL.OPERATOR,
      text,
      at: new Date().toISOString(),
    };
  }

  streamAgentTurns(
    _sessionId: string,
    handlers: StudioStreamHandlers,
  ): StudioStream {
    // Announce the honest state; emit nothing else. No canned agent script.
    handlers.onStatus?.('preview');
    return {
      status: 'preview',
      close() {
        /* no-op: nothing was opened */
      },
    };
  }

  async getPlanDoc(sessionId: string): Promise<PlanDoc> {
    return {
      id: `plan_preview_${sessionId}`,
      sessionId,
      version: 0,
      title: 'Campaign plan (preview)',
      // An explicit, bracketed scaffold — NOT agent-generated content. The panel
      // also renders a "not connected" banner above this body.
      body: [
        '# Campaign plan — PREVIEW (not generated by live agents)',
        '',
        'This document is an empty scaffold. When the studio backend is wired,',
        'the multi-agent team will co-author this plan with you and the sections',
        'below will fill with sourced, citation-grounded content.',
        '',
        '## Brief',
        '- Goal: [to be captured]',
        '- Audience: [to be captured]',
        '- Channels: [to be captured]',
        '',
        '## Angles / hooks',
        '- [strategist proposes once wired]',
        '',
        '## Drafts',
        '- [copywriter drafts once wired]',
        '',
        '## Validation & jury',
        '- [validators + cross-family jury report once wired]',
      ].join('\n'),
      status: 'draft',
      updatedAt: new Date().toISOString(),
    };
  }

  async savePlanDoc(_sessionId: string, _body: string): Promise<PlanDoc> {
    throw new StudioNotWiredError('savePlanDoc');
  }

  async approvePlan(_sessionId: string): Promise<PlanDoc> {
    throw new StudioNotWiredError('approvePlan');
  }
}

// --- live (wired) implementation -------------------------------------------

/** The studio host is ONE conversational agent — labeled plainly, never dressed
 *  up as a multi-role team. Rendered on the SYSTEM channel with this label. */
const STUDIO_HOST_LABEL = 'Studio Host';

/** Backend row shape (strawberry auto-camel-cases snake_case fields). */
interface BackendChatTurn {
  id: string;
  sessionId: string;
  seq: number;
  role: string; // 'operator' | 'host'
  text: string;
  model: string | null;
  createdAt: string;
}

function backendToChatTurn(t: BackendChatTurn): ChatTurn {
  const isOperator = t.role === 'operator';
  return {
    id: t.id,
    role: isOperator ? 'OPERATOR' : 'SYSTEM',
    label: isOperator ? STUDIO_ROLE_LABEL.OPERATOR : STUDIO_HOST_LABEL,
    text: t.text,
    at: t.createdAt,
  };
}

const SEND_CHAT_MESSAGE = `mutation SendChatMessage($sessionId: String!, $text: String!) {
  sendChatMessage(sessionId: $sessionId, text: $text) {
    operator { id sessionId seq role text model createdAt }
    host { id sessionId seq role text model createdAt }
  }
}`;

const PROBE_QUERY = `query StudioProbe { __typename }`;

const STUDIO_CHAT_HISTORY = `query StudioChatHistory($sessionId: String!) {
  studioChatHistory(sessionId: $sessionId) { id sessionId seq role text model createdAt }
}`;

async function gqlFetch<T>(
  url: string,
  query: string,
  variables: Record<string, unknown>,
): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query, variables }),
  });
  if (!res.ok) throw new Error(`studio gql HTTP ${res.status}`);
  const json = (await res.json()) as { data?: T; errors?: Array<{ message?: string }> };
  if (json.errors?.length) {
    throw new Error(json.errors[0]?.message ?? 'studio gql error');
  }
  if (!json.data) throw new Error('studio gql: empty response');
  return json.data;
}

/**
 * LiveStudioAdapter — wired to the real studio backend (GraphQL at
 * `graphqlUrl`). `sendChatMessage` persists the operator turn, calls the REAL
 * studio-host agent (a real LLM call), persists the host turn, and delivers the
 * host reply through the registered stream handler — never a canned reply.
 *
 * HONESTY: if the backend is unreachable, the reachability probe flips the
 * stream to the honest `'preview'` state and `sendChatMessage` rejects (the FE
 * catches it) — so an unreachable backend shows the not-connected state, never a
 * fabricated agent reply. This slice ships ONE conversational host; multi-role
 * brainstorm, tool/citation grounding, and plan approval are later slices.
 */
export class LiveStudioAdapter implements StudioAdapter {
  readonly source = 'live' as const;
  private readonly handlers = new Map<string, StudioStreamHandlers>();

  constructor(private readonly graphqlUrl: string) {}

  async sendChatMessage(sessionId: string, text: string): Promise<ChatTurn> {
    // REAL round-trip: operator turn + host LLM call + host turn, all persisted
    // server-side. A throw here (backend down / error) propagates so the FE
    // shows the honest state — no fake reply is ever synthesized client-side.
    const data = await gqlFetch<{
      sendChatMessage: { operator: BackendChatTurn; host: BackendChatTurn };
    }>(this.graphqlUrl, SEND_CHAT_MESSAGE, { sessionId, text });

    const operator = backendToChatTurn(data.sendChatMessage.operator);
    const host = backendToChatTurn(data.sendChatMessage.host);

    // The interface contract: the agent reply arrives via streamAgentTurns, not
    // as this call's return. Deliver the real host turn on a macrotask so it
    // lands AFTER the caller appends the returned operator turn (operator → host
    // order in the chat).
    const onTurn = this.handlers.get(sessionId)?.onTurn;
    if (onTurn) setTimeout(() => onTurn(host), 0);

    return operator;
  }

  streamAgentTurns(
    sessionId: string,
    handlers: StudioStreamHandlers,
  ): StudioStream {
    this.handlers.set(sessionId, handlers);
    let closed = false;
    let current: StudioStreamStatus = 'connecting';
    const setStatus = (s: StudioStreamStatus) => {
      current = s;
      handlers.onStatus?.(s);
    };
    setStatus('connecting');

    // Reachability probe + history restore. Reachable → replay the persisted
    // conversation (so a reload/reconnect rebuilds the chat from the store) then
    // 'open'. Unreachable → 'preview' (honest not-connected note; emits NO turns).
    void (async () => {
      try {
        const data = await gqlFetch<{ studioChatHistory: BackendChatTurn[] }>(
          this.graphqlUrl,
          STUDIO_CHAT_HISTORY,
          { sessionId },
        );
        if (closed) return;
        for (const t of data.studioChatHistory) {
          handlers.onTurn?.(backendToChatTurn(t));
        }
        setStatus('open');
      } catch {
        if (!closed) setStatus('preview');
      }
    })();

    return {
      get status() {
        return closed ? 'closed' : current;
      },
      close: () => {
        closed = true;
        this.handlers.delete(sessionId);
      },
    };
  }

  async getPlanDoc(sessionId: string): Promise<PlanDoc> {
    // Slice 1 ships the conversational host only. The plan/spec surface is real
    // and editable locally, but co-authoring + Approve/Execute are a later slice
    // — say so plainly rather than implying the doc is agent-generated.
    return {
      id: `plan_live_${sessionId}`,
      sessionId,
      version: 0,
      title: 'Campaign brief (live · plan tools land in a later slice)',
      body: [
        '# Campaign brief — shaped in the chat (live host)',
        '',
        'Slice 1 wires ONE real conversational Studio Host: send a message in the',
        'chat and it replies with a real model call and clarifying questions to',
        'build this brief. Plan co-authoring, save, and Approve/Execute are',
        'intentionally NOT wired yet — they arrive in a later slice.',
        '',
        '## Brief (capture from the conversation)',
        '- Goal:',
        '- Audience:',
        '- Channels:',
        '- Offer / promotion:',
        '- Timing:',
        '- Constraints / brand voice:',
      ].join('\n'),
      status: 'draft',
      updatedAt: new Date().toISOString(),
    };
  }

  async savePlanDoc(_sessionId: string, _body: string): Promise<PlanDoc> {
    throw new Error(
      'LiveStudioAdapter: plan save is deferred to a later slice — Slice 1 ships ' +
        'the conversational Studio Host only.',
    );
  }

  async approvePlan(_sessionId: string): Promise<PlanDoc> {
    throw new Error(
      'LiveStudioAdapter: plan approval is deferred to a later slice — Slice 1 ' +
        'ships the conversational Studio Host only.',
    );
  }
}

/**
 * Factory mirroring lib/data createAdapter(): returns the LiveStudioAdapter when
 * a studio GraphQL endpoint is configured (NEXT_PUBLIC_STUDIO_GRAPHQL_URL), and
 * the labeled PreviewStudioAdapter otherwise. The live adapter itself probes
 * reachability and degrades to the honest preview state if the backend is down,
 * so a configured-but-unreachable backend never fabricates a reply.
 */
export function createStudioAdapter(): StudioAdapter {
  const url =
    typeof process !== 'undefined'
      ? process.env.NEXT_PUBLIC_STUDIO_GRAPHQL_URL
      : undefined;
  if (url && url.length > 0) {
    return new LiveStudioAdapter(url);
  }
  return new PreviewStudioAdapter();
}
