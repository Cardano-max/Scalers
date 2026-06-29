/**
 * Typed FE models — the kkg.4 GraphQL contract (scalers-backend-plan §1.1) made
 * into TypeScript, plus the handoff "Activity" extensions (reasoning spans,
 * engagement, thread/comments) that resolve via the kkg.4 observability reads.
 *
 * Field names are STABLE per eng1. This is the single source of truth every
 * screen binds to; the mock adapter and the live (urql/SSE) adapter both
 * produce exactly these shapes, so swapping mock -> live changes no component.
 */

// --- enums (string unions mirror the GraphQL enums) ---
export type Channel = 'GMAIL' | 'INSTAGRAM' | 'FACEBOOK';
export type ActionType = 'OUTREACH' | 'COMMENT' | 'POST' | 'DM';
export type Worker =
  | 'OUTREACH'
  | 'RESPONDER'
  | 'PUBLISHER'
  | 'JURY'
  | 'CLASSIFIER'
  | 'SAFETY'
  | 'MAILBOX_MCP'
  | 'META_MCP'
  | 'WEBHOOK'
  | 'TEMPORAL'
  | 'RESEARCH'
  | 'STRATEGIST'
  | 'COPYWRITER';
export type ActionStatus =
  | 'PENDING'
  | 'APPROVED'
  | 'REJECTED'
  | 'SENT'
  | 'REGENERATING';
export type EscalationKind =
  | 'CONFIDENCE'
  | 'SAFETY'
  | 'SPLIT'
  | 'GATE'
  | 'INTENT'
  | 'WEAK_PERSONALIZATION';
export type AutonomyMode = 'AUTO' | 'APPROVE_FIRST';
export type RunTrigger = 'SCHEDULE' | 'COMMAND' | 'EVENT';
export type RunStatus = 'RUNNING' | 'SUCCESS' | 'FAILED';
export type Severity = 'INFO' | 'SUCCESS' | 'WARN' | 'ERROR';
export type Role = 'OPERATOR' | 'ASSISTANT';
export type EngineState = 'RUNNING' | 'PAUSED';
export type SpanKind = 'tool' | 'llm' | 'jury' | 'gate' | 'decision';

// --- decision sub-objects ---
export interface Escalation {
  kind: EscalationKind;
  label: string;
}
export interface JuryDim {
  label: string; // "Brand voice" | "Safety" | "Appropriateness"
  score: number;
}
export interface JuryDecision {
  confidence: number;
  threshold: number;
  agreement: string;
  dimensions: JuryDim[];
}
export interface Gate {
  label: string; // Suppression, Rate cap, PII redaction, Tenant policy, ...
  ok: boolean;
}
export interface Span {
  kind: SpanKind; // tool | llm | jury | gate | decision
  title: string;
  ms?: number;
  detail: string;
}
export interface RunEvent {
  worker: Worker;
  text: string;
  severity: Severity;
  ms: string;
  spans: Span[];
}
export interface Judge {
  name: string;
  score: number;
  vote: string; // 'pass' | 'fail'
  reasoning: string;
}
export interface ExecutionTrace {
  id: string;
  latency: string;
  model: string;
  tokens: string;
}
export interface ActivityLink {
  label: string;
  target: string;
  targetType: string; // 'POST' | 'COMMENT' | 'DM' | 'EMAIL'
}

// --- core entities ---
export interface AutonomyConfig {
  channel: Channel;
  mode: AutonomyMode;
  threshold: number;
  /**
   * Whether this channel is currently HELD (bead 439). When held, the console
   * shows the dial/mode but the backend refuses to switch it to AUTO — the FE
   * never enables auto locally; the dial is display-or-request-only.
   */
  held: boolean;
}

export interface Tenant {
  id: string;
  name: string; // "Northwind Heating & Air"
  pack: string; // "HVAC PACK"
  channels: Channel[];
  autonomy: AutonomyConfig[];
  engineState: EngineState;
}

export interface Action {
  id: string; // act_8f2a1
  tenantId: string;
  type: ActionType;
  channel: Channel;
  worker: Worker;
  target: string;
  createdAt: string; // ISO DateTime
  subject?: string | null;
  context?: string | null;
  draft: string;
  confidence: number;
  threshold: number;
  escalation: Escalation;
  jury: JuryDecision;
  gates: Gate[];
  recommendation?: string | null;
  idempotencyKey: string;
  status: ActionStatus;
}

export interface RunStep {
  at: string;
  text: string;
  state: string;
}

export interface Run {
  id: string; // run_4821
  tenantId: string;
  type: string;
  trigger: RunTrigger;
  status: RunStatus;
  startedAt: string;
  duration?: string | null;
  autoCount: number;
  reviewCount: number;
  retries: number;
  idempotencyKey: string;
  channels: Channel[];
  trajectory: RunStep[];
  note?: string | null;
  events?: RunEvent[];
}

export interface FeedEvent {
  id: string;
  tenantId: string;
  worker: Worker;
  text: string;
  at: string;
  chip?: string | null;
  severity: Severity;
}

export interface Kpis {
  autonomyPct: number;
  reviewQueueCount: number;
  outreachToday: number;
  complaintsPct: number;
  commentsAuto: number;
  commentsReview: number;
  postsPublished: number;
  postsScheduled: number;
}

export interface SystemHealth {
  emailComplaintRate: number;
  emailBounceRate: number;
  gmailWarmupUsed: number;
  gmailWarmupCap: number;
  igPublishUsed: number;
  igPublishCap: number;
  checkpointStatus: string;
}

export interface ChatMessage {
  id: string;
  role: Role;
  text: string;
  label?: string | null;
  at: string;
}

/** `overview(tenantId)` composite — kpis + attention + recentRuns + health + feed preview. */
export interface Overview {
  kpis: Kpis;
  attention: Action[];
  recentRuns: Run[];
  systemHealth: SystemHealth;
  feedPreview: FeedEvent[];
}

// --- handoff "Activity" extensions (executed actions; resolve via kkg.4 spans) ---
export interface EngagementTile {
  label: string;
  value: string;
}
export interface ThreadMessage {
  role: 'in' | 'out';
  name?: string;
  text: string;
}
export interface CommentItem {
  name: string;
  text: string;
  autoReplied: boolean;
}
/**
 * An executed action as shown on the Activity screen — the kkg.4 read API
 * resolves the reasoning trace (`thinking`), `engagement`, `outcome`, and the
 * `thread`/`comments` deep-links. Built on the same `Action` core.
 */
export interface ActivityItem extends Action {
  autonomy: AutonomyMode;
  content: string;
  outcome: { label: string; kind: 'success' | 'teal' | 'neutral' };
  thinking: string[];
  engagement: EngagementTile[];
  thread?: ThreadMessage[];
  comments?: CommentItem[];
  runId?: string | null;
  trace?: ExecutionTrace | null;
  judges: Judge[];
  spans: Span[];
  links: ActivityLink[];
}

// --- filter inputs (mirror ActionFilter / RunFilter / FeedFilter) ---
export type ActionFilter = { type?: ActionType | null } | null;
export type RunFilter = { status?: RunStatus | null } | null;
export type FeedFilter = { worker?: Worker | null } | null;
