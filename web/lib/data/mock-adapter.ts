/**
 * MOCK DataAdapter — CLEARLY LABELED. Serves kkg.4-contract-shaped data with NO
 * backend so the console renders real-looking screens until the kkg.4 GraphQL +
 * SSE API ships (eng1). It implements the SAME `DataAdapter` interface as the
 * live adapter, so swapping mock -> live (NEXT_PUBLIC_DATA_SOURCE=live) changes
 * no component. Seed data follows the handoff tenant: Northwind Heating & Air
 * (HVAC pack). This is mock data, never fabricated-in-component data — every
 * value flows through the typed models, exactly as the live path will.
 *
 * SAFETY: the seed reflects the 439 HOLD — autonomy channels are APPROVE_FIRST
 * and `held: true`; nothing auto-fires. The mock never lets the UI flip to AUTO.
 */
import type { DataAdapter } from './adapter';
import type { SSEClient, SSEHandlers, SSEStatus } from './sse';
import type {
  Action,
  AutonomyConfig,
  AutonomyMode,
  Channel,
  ActionFilter,
  ChatMessage,
  EngineState,
  FeedEvent,
  FeedFilter,
  Overview,
  Run,
  RunFilter,
  SystemHealth,
  Tenant,
  Worker,
} from './models';

const TENANT_ID = 'northwind';

const TENANT: Tenant = {
  id: TENANT_ID,
  name: 'Northwind Heating & Air',
  pack: 'HVAC PACK',
  channels: ['GMAIL', 'INSTAGRAM', 'FACEBOOK'],
  engineState: 'RUNNING',
  // 439 HOLD: every channel is approve-first and held; nothing auto-executes.
  autonomy: [
    { channel: 'GMAIL', mode: 'APPROVE_FIRST', threshold: 0.85, held: true },
    { channel: 'INSTAGRAM', mode: 'APPROVE_FIRST', threshold: 0.9, held: true },
    { channel: 'FACEBOOK', mode: 'APPROVE_FIRST', threshold: 0.88, held: true },
  ],
};

function action(partial: Partial<Action> & Pick<Action, 'id' | 'type' | 'channel' | 'worker' | 'target' | 'draft' | 'confidence' | 'threshold' | 'escalation' | 'idempotencyKey'>): Action {
  return {
    tenantId: TENANT_ID,
    createdAt: '2026-06-29T13:40:00Z',
    subject: null,
    context: null,
    recommendation: null,
    status: 'PENDING',
    jury: {
      confidence: partial.confidence,
      threshold: partial.threshold,
      agreement: 'split',
      dimensions: [
        { label: 'Brand voice', score: 0.82 },
        { label: 'Safety', score: 0.95 },
        { label: 'Appropriateness', score: 0.74 },
      ],
    },
    gates: [
      { label: 'Suppression', ok: true },
      { label: 'Rate cap', ok: true },
      { label: 'PII redaction', ok: true },
      { label: 'Tenant policy', ok: true },
    ],
    ...partial,
  };
}

const REVIEW_QUEUE: Action[] = [
  action({
    id: 'act_8f2a1',
    type: 'OUTREACH',
    channel: 'GMAIL',
    worker: 'OUTREACH',
    target: 'Bayside Property Group · Renee Calderon, Portfolio Mgr',
    subject: 'Keeping Bayside’s 14 properties comfortable this summer',
    draft:
      'Hi Renee — noticed Bayside manages 14 multifamily properties across the East Bay...',
    confidence: 0.78,
    threshold: 0.85,
    escalation: { kind: 'CONFIDENCE', label: 'Below threshold' },
    idempotencyKey: 'nw:outreach:bayside-pg:c8821',
  }),
  action({
    id: 'act_3c7b9',
    type: 'COMMENT',
    channel: 'INSTAGRAM',
    worker: 'RESPONDER',
    target: '@coastal_eats · comment on Reel "Summer AC tune-up"',
    context: 'Do you all service rooftop units for restaurants?',
    draft: 'We do! Rooftop RTUs are a big part of our commercial work...',
    confidence: 0.71,
    threshold: 0.9,
    escalation: { kind: 'SPLIT', label: 'Jury split' },
    idempotencyKey: 'nw:comment:ig:coastal-eats:r41',
  }),
  action({
    id: 'act_5d1e4',
    type: 'POST',
    channel: 'FACEBOOK',
    worker: 'PUBLISHER',
    target: 'Scheduled post · "Beat the heat: 5 AC myths"',
    draft: 'Myth #1: bigger AC = better. Not quite — oversizing short-cycles...',
    confidence: 0.66,
    threshold: 0.88,
    escalation: { kind: 'SAFETY', label: 'Safety veto' },
    idempotencyKey: 'nw:post:fb:ac-myths:w26',
    gates: [
      { label: 'Suppression', ok: true },
      { label: 'Rate cap', ok: true },
      { label: 'Pricing claim', ok: false },
      { label: 'Media format', ok: true },
    ],
  }),
];

const RUNS: Run[] = [
  {
    id: 'run_4821',
    tenantId: TENANT_ID,
    type: 'Comment reply',
    trigger: 'EVENT',
    status: 'SUCCESS',
    startedAt: '2026-06-29T13:31:00Z',
    duration: '4.2s',
    autoCount: 0,
    reviewCount: 1,
    retries: 0,
    idempotencyKey: 'nw:comment:ig:coastal-eats:r41',
    channels: ['INSTAGRAM'],
    note: null,
    trajectory: [
      { at: '13:31:00', text: 'Webhook · comment received', state: 'done' },
      { at: '13:31:01', text: 'Classifier · intent=question', state: 'done' },
      { at: '13:31:02', text: 'Responder · draft reply', state: 'done' },
      { at: '13:31:03', text: 'Jury · split → escalate', state: 'done' },
    ],
  },
  {
    id: 'run_4820',
    tenantId: TENANT_ID,
    type: 'Outreach batch',
    trigger: 'SCHEDULE',
    // NOTE: handoff shows a "Partial" status; backend RunStatus enum has no
    // PARTIAL (RUNNING|SUCCESS|FAILED) — flagged to eng1. Modeled as SUCCESS
    // with a note + a nonzero review/deferred count, which conveys the same.
    status: 'SUCCESS',
    startedAt: '2026-06-29T09:00:00Z',
    duration: '2m 11s',
    autoCount: 0,
    reviewCount: 6,
    retries: 1,
    idempotencyKey: 'nw:outreach:batch:0900',
    channels: ['GMAIL'],
    note: 'Rate cap hit on domain warmup — 6 deferred',
    trajectory: [
      { at: '09:00:00', text: 'Temporal · batch start', state: 'done' },
      { at: '09:00:05', text: 'Suppression · filter list', state: 'done' },
      { at: '09:01:40', text: 'Outreach · 6 drafts → review', state: 'done' },
      { at: '09:02:11', text: 'Rate cap · 6 deferred', state: 'warn' },
    ],
  },
];

const KPIS = {
  autonomyPct: 0.87,
  reviewQueueCount: REVIEW_QUEUE.length,
  outreachToday: 42,
  complaintsPct: 0.0006,
  commentsAuto: 58,
  commentsReview: 6,
  postsPublished: 1,
  postsScheduled: 3,
};

const SYSTEM_HEALTH: SystemHealth = {
  emailComplaintRate: 0.0006,
  emailBounceRate: 0.012,
  gmailWarmupUsed: 42,
  gmailWarmupCap: 60,
  igPublishUsed: 6,
  igPublishCap: 100,
  checkpointStatus: 'healthy',
};

let FEED_SEQ = 100;
const FEED_POOL: Array<Omit<FeedEvent, 'id' | 'tenantId' | 'at'>> = [
  { worker: 'OUTREACH', text: 'Drafted outreach to Bayside Property Group', chip: 'Escalated', severity: 'WARN' },
  { worker: 'JURY', text: 'Jury split on @coastal_eats reply (0.71 / 0.90)', chip: 'Escalated', severity: 'WARN' },
  { worker: 'RESPONDER', text: 'Replied to comment on "Summer AC tune-up"', chip: 'Sent', severity: 'SUCCESS' },
  { worker: 'SAFETY', text: 'Vetoed FB post — pricing claim gate failed', chip: 'Escalated', severity: 'ERROR' },
  { worker: 'TEMPORAL', text: 'Checkpoint healthy', chip: null, severity: 'INFO' },
  { worker: 'MAILBOX_MCP', text: 'Warmup send 42/60 today', chip: null, severity: 'INFO' },
];

function feedEvent(seed: Omit<FeedEvent, 'id' | 'tenantId' | 'at'>): FeedEvent {
  FEED_SEQ += 1;
  return {
    ...seed,
    id: `feed_${FEED_SEQ}`,
    tenantId: TENANT_ID,
    at: '2026-06-29T13:40:00Z',
  };
}

const FEED: FeedEvent[] = FEED_POOL.map(feedEvent);

function filterByType<T extends { type: string }>(items: T[], filter?: ActionFilter): T[] {
  if (!filter || !filter.type) return items;
  return items.filter((i) => i.type === filter.type);
}

/** Internal engine-state cell so pause/resume reflects across calls in the mock. */
let engineState: EngineState = TENANT.engineState;

export class MockAdapter implements DataAdapter {
  readonly source = 'mock' as const;

  async getTenant(id: string) {
    return id === TENANT_ID ? { ...TENANT, engineState } : null;
  }
  async getOverview(_tenantId: string): Promise<Overview> {
    return {
      kpis: { ...KPIS },
      attention: REVIEW_QUEUE,
      recentRuns: RUNS,
      systemHealth: { ...SYSTEM_HEALTH },
      feedPreview: FEED.slice(0, 5),
    };
  }
  async getReviewQueue(_tenantId: string, filter?: ActionFilter) {
    return filterByType(REVIEW_QUEUE, filter);
  }
  async getAction(id: string) {
    return REVIEW_QUEUE.find((a) => a.id === id) ?? null;
  }
  async getRuns(_tenantId: string, filter?: RunFilter) {
    if (!filter || !filter.status) return RUNS;
    return RUNS.filter((r) => r.status === filter.status);
  }
  async getRun(id: string) {
    return RUNS.find((r) => r.id === id) ?? null;
  }
  async getFeed(_tenantId: string, filter?: FeedFilter, _after?: string, limit?: number) {
    let items = FEED;
    if (filter?.worker) items = items.filter((f) => f.worker === filter.worker);
    return typeof limit === 'number' ? items.slice(0, limit) : items;
  }
  async getSystemHealth(_tenantId: string) {
    return { ...SYSTEM_HEALTH };
  }

  /**
   * Mock SSE: emits a `feed.event` from the pool on a timer and a periodic
   * `kpi.updated`, mirroring the live stream's cadence. Honors `close()` and
   * reports an immediate "open" status. Appends stop when closed (the real
   * stream also stops when the harness/stream is paused).
   */
  subscribe(_tenantId: string, handlers: SSEHandlers, onStatus?: (s: SSEStatus) => void): SSEClient {
    let status: SSEStatus = 'open';
    onStatus?.('open');
    let i = 0;
    const timer = setInterval(() => {
      const seed = FEED_POOL[i % FEED_POOL.length];
      i += 1;
      handlers['feed.event']?.(feedEvent(seed));
    }, 8000);
    return {
      status: () => status,
      close: () => {
        status = 'closed';
        clearInterval(timer);
        onStatus?.('closed');
      },
    };
  }

  // --- mutations (mock) — signatures mirror the DataAdapter interface ---
  async approveAction(id: string, _idempotencyKey: string): Promise<Action> {
    const a = REVIEW_QUEUE.find((x) => x.id === id);
    if (!a) throw new Error(`mock: action ${id} not found`);
    // Approve RESUMES the engine (the action leaves the queue); never bypasses a gate.
    return { ...a, status: 'APPROVED' };
  }
  async rejectAction(id: string, _reason?: string): Promise<Action> {
    const a = REVIEW_QUEUE.find((x) => x.id === id);
    if (!a) throw new Error(`mock: action ${id} not found`);
    return { ...a, status: 'REJECTED' };
  }
  async editActionDraft(id: string, draft: string): Promise<Action> {
    const a = REVIEW_QUEUE.find((x) => x.id === id);
    if (!a) throw new Error(`mock: action ${id} not found`);
    return { ...a, draft };
  }
  async regenerateAction(id: string): Promise<Action> {
    const a = REVIEW_QUEUE.find((x) => x.id === id);
    if (!a) throw new Error(`mock: action ${id} not found`);
    return { ...a, status: 'REGENERATING' };
  }
  async setEngineState(_tenantId: string, paused: boolean): Promise<EngineState> {
    engineState = paused ? 'PAUSED' : 'RUNNING';
    return engineState;
  }
  async setAutonomy(
    _tenantId: string,
    channel: Channel,
    mode: AutonomyMode,
    threshold: number,
  ): Promise<AutonomyConfig> {
    const cfg = TENANT.autonomy.find((c) => c.channel === channel)!;
    // SAFETY: a held channel cannot be switched to AUTO — the mock mirrors the
    // backend 439 gate. The request is accepted as APPROVE_FIRST only.
    if (cfg.held && mode === 'AUTO') {
      return { channel, mode: 'APPROVE_FIRST', threshold, held: true };
    }
    return { channel, mode, threshold, held: cfg.held };
  }
  async sendCommand(_tenantId: string, text: string): Promise<ChatMessage> {
    return {
      id: `msg_${Date.now()}`,
      role: 'ASSISTANT',
      text: `(mock) Acknowledged: "${text}". Wire to the harness command endpoint when kkg.4 ships.`,
      label: 'Harness',
      at: '2026-06-29T13:40:00Z',
    };
  }
}

export const MOCK_TENANT_ID = TENANT_ID;
export type { Worker };
