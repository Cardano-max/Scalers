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
// The obsapi maps known DB channels to GMAIL/INSTAGRAM/FACEBOOK and UPPERCASES
// anything else, so real campaign drafts also arrive as EMAIL/SMS/IG/REELS/TIKTOK.
// These are part of the live contract — the console renders them honestly.
export type Channel =
  | 'GMAIL'
  | 'INSTAGRAM'
  | 'FACEBOOK'
  | 'EMAIL'
  | 'SMS'
  | 'IG'
  | 'REELS'
  | 'TIKTOK';
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
  | 'COPYWRITER'
  // Multi-agent campaign run workers (studio orchestration → real run events/feed).
  | 'TEAM'
  | 'DRAFT'
  | 'CRITIC';
export type ActionStatus =
  | 'PENDING'
  | 'APPROVED'
  | 'SENDING'
  | 'REJECTED'
  | 'SENT'
  | 'FAILED'
  | 'REGENERATING';
export type EscalationKind =
  | 'CONFIDENCE'
  | 'SAFETY'
  | 'SPLIT'
  | 'GATE'
  | 'INTENT'
  | 'WEAK_PERSONALIZATION';
export type AutonomyMode = 'AUTO' | 'APPROVE_FIRST';
export type RunTrigger = 'SCHEDULE' | 'COMMAND' | 'EVENT' | 'STUDIO';
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
export interface JurorDimScore {
  judge: string;
  score: number;
  vote: string; // 'pass' | 'fail'
}
export interface JuryDim {
  label: string; // "Brand voice" | "Safety" | "Appropriateness"
  score: number;
  verdict: string; // 'pass' | 'fail'
  threshold: number;
  jurorBreakdown: JurorDimScore[];
}
/** B2: raw per-judge per-dimension vote (jury.judges). Maps 1:1 to types.py JudgeVote. */
export interface JudgeVote {
  judge: string;
  family?: string | null;
  voice: number;
  safety: number;
  appr: number;
  overall: number;
}
export interface JuryDecision {
  confidence: number;
  threshold: number;
  agreement: string;
  dimensions: JuryDim[];
  /** B1: generation-stability component (Phase-5+). null = not captured (pre-Phase-5 row). */
  selfConsistency?: number | null;
  /** B2: raw per-judge per-dimension votes from autonomy_jury. */
  judges?: JudgeVote[];
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
  actionId?: string | null;
  runId?: string | null;
  decisionId?: string | null;
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
  /**
   * The REAL provider error captured when a send FAILED (maps to
   * `actions.last_error`), e.g. a Meta/Graph `HTTP 400 #145 …` body. Present
   * only when `status === 'FAILED'`. Rendered verbatim in the failed-action UI
   * so the operator sees WHY a send failed — never fabricated or paraphrased.
   */
  lastError?: string | null;
  judges?: Judge[];
  isSeeded?: boolean;
  // --- traceability spine (additive) — REAL lineage exposed/derived on the
  // draft so every Review-queue item links both ways. All honest-null when the
  // source genuinely has no value; nothing is fabricated. ---
  /** Owning workflow run id (actions.run_id). */
  runId?: string | null;
  /** Real campaign id (agent_runs.campaign_id, or run_id convention fallback). */
  campaignId?: string | null;
  /** The producing agent's role (e.g. Copywriter), linked via agent_runs. null
   *  when the exact producing step could not be determined — never guessed. */
  agentRole?: string | null;
  /** The producing agent_runs step id, when resolvable (else null → honest). */
  agentStepId?: string | null;
  /** Run-level Langfuse trace url (per-step span ids are not persisted). */
  traceUrl?: string | null;
  /** The resolved send mode of the LAST approve→publish on this draft ('live' |
   *  'test_redirect'), surfaced on the approveAction response so the Review Queue can
   *  badge how the send was routed. Undefined/null until an approve actually sends. */
  mode?: 'live' | 'test_redirect' | null;
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
  traceUrl?: string | null;
  events?: RunEvent[];
  /** Real campaign id derived from the run (run_id convention team-{campaignId}-{uuid}),
   * authoritative fallback agent_runs.campaign_id. null when no campaign is associated. */
  campaignId?: string | null;
}

/**
 * The per-campaign SPEC DOC for one run — assembled from already-persisted REAL
 * rows (plan + agent_runs + archetype). `markdown` is rendered read-now;
 * `contentJson` is the structured JSON (string) for later editing. `runId` IS
 * the spec key (== Run.id). Every field is real or honest-null.
 */
export interface CampaignSpec {
  runId: string;
  campaignId?: string | null;
  tenantId?: string | null;
  archetypeId?: string | null;
  markdown: string;
  contentJson?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface FeedEvent {
  id: string;
  tenantId: string;
  worker: Worker;
  text: string;
  at: string;
  chip?: string | null;
  severity: Severity;
  actionId?: string | null;
  runId?: string | null;
  decisionId?: string | null;
  /** Real campaign id associated with this feed event's run (honest-null when absent). */
  campaignId?: string | null;
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

// --- evidence / provenance (GET /studio/action/{actionId}/evidence) ---
// What a staged draft ACTUALLY used: the brand-voice doc, the CSV/customer facts,
// lead memories, internal notes, cited research, tool calls, critic/jury verdicts,
// and the producing agent. REAL-ONLY: any category the draft did not genuinely use
// arrives null (objects) or [] (lists) — never a fabricated stand-in. The console
// renders these as clean chips/cards and OMITS empty categories entirely.
export interface EvidenceAgent {
  role: string | null;
  model: string | null;
  reasoningSummary: string | null;
}
export interface EvidenceBrandVoice {
  tenantId: string;
  used: boolean;
  tone: string[];
  structure: string[];
  prefer: string[];
  ban: string[];
  approvedClaims: string[];
  source: string;
}
export interface EvidenceCustomer {
  customerId: string | null;
  name: string | null;
  city: string | null;
  note: string | null;
  interest: string | null;
  lifecycle: string | null;
  lastTattooStyle: string | null;
  winBackCandidate: boolean;
  factsUsed: string[];
}
export interface EvidenceMemory {
  text: string;
  kind: string | null;
  createdAt: string | null;
}
export interface EvidenceResearchSource {
  url: string;
  title: string | null;
  snippet: string | null;
  query: string | null;
}
export interface EvidenceDocument {
  document: string;
  heading: string | null;
  documentId: string | null;
}
export interface EvidenceToolCall {
  name: string;
  detail: string | null;
}
export interface EvidenceCritic {
  verdict: string | null;
  rationale: string | null;
  model: string | null;
}
export interface EvidenceJury {
  aggregate: number | null;
  decision: string | null;
  note: string | null;
}
export interface ActionEvidence {
  actionId: string;
  runId: string | null;
  campaignId: string | null;
  tenantId: string;
  channel: string | null;
  target: string | null;
  status: string | null;
  createdBy: EvidenceAgent | null;
  brandVoice: EvidenceBrandVoice | null; // null when not genuinely used (real-only)
  customer: EvidenceCustomer | null;
  leadMemories: EvidenceMemory[];
  internalNotes: string | null;
  brandDocuments: EvidenceDocument[]; // [] when the draft used no doc passages (real-only)
  researchSources: EvidenceResearchSource[]; // [] when the draft cited none (real-only)
  toolCalls: EvidenceToolCall[];
  criticReview: EvidenceCritic | null;
  jury: EvidenceJury | null;
  confidence: number | null;
  threshold: number | null;
  confidenceReason: string | null;
  reasoningUrl: string | null;
  isRealOnly: boolean;
}
