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
  ActivityItem,
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
        {
          label: 'Brand voice',
          score: 0.82,
          verdict: 'pass',
          threshold: 0.8,
          jurorBreakdown: [
            { judge: 'Judge A', score: 0.85, vote: 'pass' },
            { judge: 'Judge B', score: 0.79, vote: 'fail' },
          ],
        },
        {
          label: 'Safety',
          score: 0.95,
          verdict: 'pass',
          threshold: 0.9,
          jurorBreakdown: [
            { judge: 'Judge A', score: 0.97, vote: 'pass' },
            { judge: 'Judge B', score: 0.93, vote: 'pass' },
          ],
        },
        {
          label: 'Appropriateness',
          score: 0.74,
          verdict: 'fail',
          threshold: 0.8,
          jurorBreakdown: [
            { judge: 'Judge A', score: 0.72, vote: 'fail' },
            { judge: 'Judge B', score: 0.76, vote: 'fail' },
          ],
        },
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

/**
 * Executed-action seed for the Activity screen. These are HISTORICAL completed
 * actions — the reasoning trace, engagement, outcome, and thread/comments the
 * `ActivityItem` model carries. The auto-executed comment reply mirrors the
 * Overview "58 auto · 6 review" comments KPI; outreach + posts went out as
 * human-approved ("You approved"), consistent with the 439 HOLD on live flips.
 */
function activityItem(
  partial: Partial<ActivityItem> &
    Pick<
      ActivityItem,
      | 'id'
      | 'type'
      | 'channel'
      | 'worker'
      | 'target'
      | 'content'
      | 'confidence'
      | 'autonomy'
      | 'outcome'
      | 'thinking'
      | 'engagement'
      | 'idempotencyKey'
    >,
): ActivityItem {
  const confidence = partial.confidence;
  const threshold = partial.threshold ?? 0.85;
  return {
    tenantId: TENANT_ID,
    createdAt: '2026-06-29T13:20:00Z',
    subject: null,
    context: null,
    recommendation: null,
    status: 'SENT',
    draft: partial.content,
    threshold,
    // Activity items already cleared the gate path; escalation is unused by the
    // Activity screen but required by the Action core — set a benign cleared value.
    escalation: { kind: 'CONFIDENCE', label: 'Cleared' },
    jury: {
      confidence,
      threshold,
      agreement: 'unanimous',
      dimensions: [
        {
          label: 'Brand voice',
          score: 0.9,
          verdict: 'pass',
          threshold: 0.8,
          jurorBreakdown: [
            { judge: 'Judge A', score: 0.92, vote: 'pass' },
            { judge: 'Judge B', score: 0.88, vote: 'pass' },
          ],
        },
        {
          label: 'Safety',
          score: 0.97,
          verdict: 'pass',
          threshold: 0.9,
          jurorBreakdown: [
            { judge: 'Judge A', score: 0.98, vote: 'pass' },
            { judge: 'Judge B', score: 0.96, vote: 'pass' },
          ],
        },
        {
          label: 'Appropriateness',
          score: 0.88,
          verdict: 'pass',
          threshold: 0.8,
          jurorBreakdown: [
            { judge: 'Judge A', score: 0.89, vote: 'pass' },
            { judge: 'Judge B', score: 0.87, vote: 'pass' },
          ],
        },
      ],
    },
    gates: [
      { label: 'Suppression', ok: true },
      { label: 'Rate cap', ok: true },
      { label: 'PII redaction', ok: true },
      { label: 'Tenant policy', ok: true },
    ],
    // v2 observability fields
    runId: `run_${Math.floor(Math.random() * 5000)}`,
    trace: {
      id: `tr_${Math.random().toString(36).slice(2, 8)}`,
      latency: `${(Math.random() * 5 + 0.5).toFixed(1)}s`,
      model: 'strong (draft) · small (route)',
      tokens: `${Math.floor(Math.random() * 2000 + 500)} in · ${Math.floor(Math.random() * 500 + 100)} out`,
    },
    judges: [
      { name: 'Judge A', score: 0.9, vote: 'pass', reasoning: 'on-brand, clear value' },
      { name: 'Judge B', score: 0.96, vote: 'pass', reasoning: 'no safety issues' },
      { name: 'Judge C', score: 0.88, vote: 'pass', reasoning: 'appropriate tone' },
    ],
    spans: [
      { kind: 'tool', title: 'ingest.data', ms: 120, detail: 'Fetched contact · verified contact details' },
      { kind: 'llm', title: 'select angle', ms: 600, detail: 'Routing: problem→solution personalization strategy' },
      { kind: 'llm', title: 'draft · strong model', ms: 2100, detail: 'Personalized on property size and age; tone direct, no hard sell' },
      { kind: 'jury', title: 'jury vote', ms: 900, detail: '3/3 pass · pooled 0.91 ≥ 0.85 threshold' },
      { kind: 'gate', title: 'pre-action hooks', ms: 40, detail: 'suppression ✓ · rate-cap ✓ · PII redaction ✓' },
      { kind: 'tool', title: 'send · MCP', ms: 310, detail: 'warmup pacing · 250 OK · idem recorded' },
      { kind: 'decision', title: 'auto-executed', detail: 'confidence cleared threshold, no veto → sent' },
    ],
    links: [],
    ...partial,
  };
}

const ACTIVITY: ActivityItem[] = [
  activityItem({
    id: 'evt_91c4d',
    type: 'OUTREACH',
    channel: 'GMAIL',
    worker: 'OUTREACH',
    autonomy: 'APPROVE_FIRST',
    target: 'Marina Bay Dental · Dr. Priya Anand, Owner',
    idempotencyKey: 'nw:outreach:marina-bay-dental:d4417',
    createdAt: '2026-06-29T11:42:00Z',
    subject: 'A quieter, more efficient HVAC for Marina Bay Dental',
    content:
      'Hi Dr. Anand — patient comfort is everything in a dental practice, and an aging rooftop unit can make the waiting room uneven and noisy. We help East Bay practices cut runtime cost ~18% with a right-sized, quiet system and a maintenance plan that keeps it that way. Open to a 15-minute look at your current setup next week?',
    confidence: 0.88,
    threshold: 0.85,
    outcome: { label: 'Sent', kind: 'success' },
    engagement: [
      { label: 'Opened', value: '3×' },
      { label: 'Replied', value: '18m' },
      { label: 'Sentiment', value: 'Positive' },
    ],
    thinking: [
      'Ingest silver.contacts → Marina Bay Dental (verified owner, East Bay).',
      'HVAC pack voice: lead with patient comfort + quiet operation, not specs.',
      'Personalized on practice type (dental) + regional climate (East Bay summer).',
      'Jury 0.88 ≥ 0.85 threshold, gates clear → drafted for operator sign-off.',
      'Operator approved → Mailbox MCP users.messages.send (idempotency-guarded).',
    ],
    thread: [
      {
        role: 'out',
        name: 'Northwind · agent',
        text: 'Hi Dr. Anand — open to a 15-minute look at your current HVAC setup next week?',
      },
      {
        role: 'in',
        name: 'Dr. Priya Anand',
        text: 'Yes — the waiting room has been a problem all summer. Thursday afternoon?',
      },
      {
        role: 'out',
        name: 'Northwind · agent',
        text: 'Thursday works. I’ll send a calendar hold for 2pm with our service lead.',
      },
    ],
  }),
  activityItem({
    id: 'evt_77a0b',
    type: 'COMMENT',
    channel: 'INSTAGRAM',
    worker: 'RESPONDER',
    autonomy: 'AUTO',
    target: '@hvac_homeowner · comment on Reel “Summer AC tune-up”',
    idempotencyKey: 'nw:comment:ig:hvac-homeowner:r88',
    createdAt: '2026-06-29T12:58:00Z',
    context: 'How often should I actually change my filter? Getting mixed advice online.',
    content:
      'Great question! For most homes a 1-inch filter every 30–60 days, or every 90 days for 4–5 inch media filters. Pets or allergies? Lean toward the shorter end. A clean filter is the cheapest way to protect your system this summer. 🔧',
    confidence: 0.93,
    threshold: 0.9,
    outcome: { label: 'Replied', kind: 'teal' },
    engagement: [
      { label: 'Likes', value: '12' },
      { label: 'Reply', value: 'auto' },
      { label: 'Sentiment', value: 'Positive' },
    ],
    thinking: [
      'Webhook → comment received on “Summer AC tune-up” Reel.',
      'Classifier: intent=question, topic=maintenance, no pricing/medical claim.',
      'Responder drafted from HVAC pack FAQ (filter cadence) + friendly voice.',
      'Jury 0.93 ≥ 0.90 IG threshold, safety clear, all gates pass → auto-replied.',
    ],
    thread: [
      {
        role: 'in',
        name: '@hvac_homeowner',
        text: 'How often should I actually change my filter? Getting mixed advice online.',
      },
      {
        role: 'out',
        name: 'Northwind · agent',
        text: 'Great question! 1-inch filters every 30–60 days, 4–5 inch media every 90. Pets/allergies → shorter end. 🔧',
      },
    ],
  }),
  activityItem({
    id: 'evt_4b2e8',
    type: 'POST',
    channel: 'FACEBOOK',
    worker: 'PUBLISHER',
    autonomy: 'APPROVE_FIRST',
    target: 'Published post · “Beat the heat: 5 AC myths”',
    idempotencyKey: 'nw:post:fb:ac-myths:p12',
    createdAt: '2026-06-29T09:15:00Z',
    content:
      'Beat the heat: 5 AC myths, busted. Myth #1: bigger = better. Oversizing short-cycles your system, wastes energy, and leaves rooms humid. Right-sizing (a real Manual J load calc) beats raw tonnage every time. Swipe for myths #2–5. ☀️❄️',
    confidence: 0.91,
    threshold: 0.88,
    outcome: { label: 'Published', kind: 'success' },
    engagement: [
      { label: 'Likes', value: '142' },
      { label: 'Comments', value: '11' },
      { label: 'Reach', value: '3.4k' },
      { label: 'Saves', value: '27' },
    ],
    thinking: [
      'Strategist scheduled an educational “myth-busting” post (HVAC pack calendar).',
      'Copywriter drafted; Publisher checked Meta media/format + pricing-claim gate.',
      'Jury 0.91 ≥ 0.88 FB threshold; operator approved the publish.',
      'Meta MCP published; engagement agent now watching the comment thread.',
    ],
    comments: [
      {
        name: 'Dana R.',
        text: 'So a bigger unit isn’t better? My neighbor swears by his huge one.',
        autoReplied: true,
      },
      {
        name: 'Marcus T.',
        text: 'What’s a Manual J load calc and do you do them?',
        autoReplied: true,
      },
      {
        name: 'Priya S.',
        text: 'Saved this — our upstairs is always humid in July.',
        autoReplied: false,
      },
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
    events: [
      {
        worker: 'WEBHOOK',
        text: '14 comment events received · deduped',
        severity: 'INFO',
        ms: '0.0s',
        spans: [
          { kind: 'tool', title: 'webhook.ingest', ms: 12, detail: 'Meta webhook · 14 events · deduped on event id → 14 unique' },
        ],
      },
      {
        worker: 'CLASSIFIER',
        text: 'Sorted 12 routine · 2 ambiguous',
        severity: 'INFO',
        ms: '2.1s',
        spans: [
          { kind: 'llm', title: 'classify ×14 · small model', ms: 2100, detail: '12 routine-positive · 2 ambiguous questions' },
        ],
      },
      {
        worker: 'JURY',
        text: '12 ≥ 0.88 · auto-approved',
        severity: 'SUCCESS',
        ms: '7.4s',
        spans: [
          { kind: 'jury', title: 'jury vote ×12 · 3 judges', ms: 7400, detail: 'cross-family · 12 pooled ≥ 0.88' },
          { kind: 'decision', title: 'route', detail: '12 → auto-reply · 2 → review' },
        ],
      },
      {
        worker: 'RESPONDER',
        text: 'Replying to 12 via Meta MCP',
        severity: 'SUCCESS',
        ms: 'running',
        spans: [
          { kind: 'gate', title: 'jitter + rate', detail: '40–90s randomized per reply · IG+FB caps ok' },
          { kind: 'tool', title: 'meta.reply ×12', detail: 'idem nw:reply:ig|fb:<comment_id>' },
        ],
      },
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
    events: [
      {
        worker: 'TEMPORAL',
        text: 'Run started · outreach batch',
        severity: 'INFO',
        ms: '0.0s',
        spans: [
          { kind: 'tool', title: 'temporal.start', detail: 'durable workflow · idem nw:out:batch:wk26-d4' },
        ],
      },
      {
        worker: 'OUTREACH',
        text: 'Ingested 24 contacts from silver',
        severity: 'INFO',
        ms: '0.3s',
        spans: [
          { kind: 'tool', title: 'silver.contacts.query', ms: 310, detail: 'tenant=northwind · segment=property-mgr → 24 rows' },
          { kind: 'gate', title: 'suppression filter', ms: 20, detail: '0 on do-not-contact list' },
        ],
      },
      {
        worker: 'OUTREACH',
        text: 'Drafted 24 personalized emails',
        severity: 'INFO',
        ms: '41s',
        spans: [
          { kind: 'llm', title: 'draft ×24 · strong model', ms: 38000, detail: 'avg 1.6s each · personalized on unit count + building age' },
        ],
      },
      {
        worker: 'JURY',
        text: 'Scored 24 · 19 ≥ 0.85 · 5 below',
        severity: 'WARN',
        ms: '18s',
        spans: [
          { kind: 'jury', title: 'jury vote ×24 · 3 judges', ms: 18000, detail: '19 pooled ≥ 0.85 threshold' },
          { kind: 'decision', title: 'route', detail: '19 → auto-send · 5 → review' },
        ],
      },
      {
        worker: 'MAILBOX_MCP',
        text: 'Sent 19 via Gmail',
        severity: 'SUCCESS',
        ms: '6s',
        spans: [
          { kind: 'gate', title: 'rate cap', detail: '12/60 warmup window ok' },
          { kind: 'tool', title: 'mailbox.send ×19', ms: 5900, detail: 'warmup pacing · all 250 OK · idem keys recorded' },
        ],
      },
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
  async getActivity(_tenantId: string, filter?: ActionFilter) {
    return filterByType(ACTIVITY, filter);
  }
  async getActivityItem(id: string) {
    return ACTIVITY.find((a) => a.id === id) ?? null;
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
  async startCampaign(
    _tenantId: string,
    _brief: { goal: string; audience: string; channels: string[]; constraints?: string; hooks?: string[] },
  ): Promise<{ runId: string; actionIds: string[]; status: string }> {
    return {
      runId: `mock-campaign-${Date.now()}`,
      actionIds: ['mock_act_1', 'mock_act_2', 'mock_act_3'],
      status: 'PENDING',
    };
  }
}

export const MOCK_TENANT_ID = TENANT_ID;
export type { Worker };
