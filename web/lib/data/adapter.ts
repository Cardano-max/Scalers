/**
 * DataAdapter — the one interface every screen reads/writes through. The mock
 * adapter and the live (urql + SSE) adapter both implement it, so flipping
 * NEXT_PUBLIC_DATA_SOURCE=mock|live swaps the backend without touching a single
 * component. Read methods resolve queries; `subscribe` opens the typed SSE
 * stream; mutations carry idempotency and (for the engine-driving ones) RESUME
 * the harness — they never bypass a gate.
 */
import type {
  Action,
  ActionEvidence,
  ActionLineage,
  ActivityItem,
  AutonomyConfig,
  AutonomyMode,
  Channel,
  ActionFilter,
  CampaignExamplesPage,
  CampaignSpec,
  EngineState,
  FeedEvent,
  FeedFilter,
  Overview,
  Run,
  RunFilter,
  SystemHealth,
  Tenant,
  TenantMeta,
  ChatMessage,
} from './models';
import type { SSEClient, SSEHandlers, SSEStatus } from './sse';

export interface DataAdapter {
  /** Human-facing label for the active source ("mock" | "live") — shown in the UI. */
  readonly source: 'mock' | 'live';

  // --- reads (kkg.4 queries) ---
  getTenant(id: string): Promise<Tenant | null>;
  getOverview(tenantId: string): Promise<Overview>;
  getReviewQueue(tenantId: string, filter?: ActionFilter): Promise<Action[]>;
  getAction(id: string): Promise<Action | null>;
  /**
   * The EVIDENCE / PROVENANCE for one staged draft — what it ACTUALLY used: the
   * brand-voice doc, customer/CSV facts, lead memories, internal notes, cited
   * research, tool calls, critic/jury verdicts, and the producing agent. REAL-ONLY:
   * categories the draft did not genuinely use arrive null / []. Resolves null
   * (honest) when no evidence was captured for the id.
   */
  getActionEvidence(actionId: string): Promise<ActionEvidence | null>;
  /**
   * Server-driven tenant safety flags (ju1.5): the TEST-MODE banner + send-disable
   * state come from here, never from a hardcoded tenant list. Honest-null on
   * transport failure (the UI then shows no banner rather than a guessed one —
   * the SERVER gate still refuses the send regardless).
   */
  getTenantMeta(tenantId: string): Promise<TenantMeta | null>;
  /**
   * The tenant's campaign-example MEMORY (ju1.2 library): real transcribed
   * examples + extracted patterns, honest-empty when none.
   */
  getCampaignExamples(tenantId: string): Promise<CampaignExamplesPage>;
  /**
   * Draft lineage for the review queue (ju1.5): source CSV / customer / artist /
   * studio / offer / CTA / examples referenced. Null fields = honest "missing".
   */
  getActionLineage(actionId: string): Promise<ActionLineage | null>;
  /**
   * Executed (completed) actions for the Activity screen — the reasoning trace,
   * engagement, outcome, and thread/comments deep-links resolved alongside the
   * Action core. Same `ActionFilter` (by type) as the review queue.
   */
  getActivity(
    tenantId: string,
    filter?: ActionFilter,
  ): Promise<ActivityItem[]>;
  getActivityItem(id: string): Promise<ActivityItem | null>;
  getRuns(tenantId: string, filter?: RunFilter): Promise<Run[]>;
  getRun(id: string): Promise<Run | null>;
  /**
   * The per-campaign SPEC DOC for a run, assembled from already-persisted REAL
   * rows (plan + agent_runs + archetype). Resolves null (honest-null) when the
   * run has no spec and nothing to reconstruct. `runId` IS the spec key.
   */
  getCampaignSpec(runId: string): Promise<CampaignSpec | null>;
  getFeed(
    tenantId: string,
    filter?: FeedFilter,
    after?: string,
    limit?: number,
  ): Promise<FeedEvent[]>;
  getSystemHealth(tenantId: string): Promise<SystemHealth>;

  // --- realtime (kkg.4 SSE) ---
  subscribe(
    tenantId: string,
    handlers: SSEHandlers,
    onStatus?: (s: SSEStatus) => void,
  ): SSEClient;

  // --- mutations (surface ready for the action/command/dial beads) ---
  // `live` (default false = safe redirect) is the operator's explicit live-send
  // authorization; the resolved `mode` rides back on the returned Action.
  approveAction(id: string, idempotencyKey: string, live?: boolean): Promise<Action>;
  rejectAction(id: string, reason?: string): Promise<Action>;
  editActionDraft(id: string, draft: string): Promise<Action>;
  regenerateAction(id: string): Promise<Action>;
  /** Pause/Resume the harness (master control). Returns the new engine state. */
  setEngineState(tenantId: string, paused: boolean): Promise<EngineState>;
  /**
   * Request a per-channel autonomy change. SAFETY: the backend refuses to set
   * AUTO while a channel is HELD (bead 439); the FE never enables auto locally.
   * This is the display-or-request path only — eval+calibration gate the real flip.
   */
  setAutonomy(
    tenantId: string,
    channel: Channel,
    mode: AutonomyMode,
    threshold: number,
  ): Promise<AutonomyConfig>;
  sendCommand(tenantId: string, text: string): Promise<ChatMessage>;
  /**
   * Start a campaign with the given brief. Returns the campaign run ID, action IDs
   * generated, and the initial status.
   */
  startCampaign(
    tenantId: string,
    brief: { goal: string; audience: string; channels: string[]; constraints?: string; hooks?: string[] },
  ): Promise<{ runId: string; actionIds: string[]; status: string }>;
}
