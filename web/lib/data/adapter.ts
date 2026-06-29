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
  AutonomyConfig,
  AutonomyMode,
  Channel,
  ActionFilter,
  EngineState,
  FeedEvent,
  FeedFilter,
  Overview,
  Run,
  RunFilter,
  SystemHealth,
  Tenant,
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
  getRuns(tenantId: string, filter?: RunFilter): Promise<Run[]>;
  getRun(id: string): Promise<Run | null>;
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
  approveAction(id: string, idempotencyKey: string): Promise<Action>;
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
}
