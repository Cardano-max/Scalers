/**
 * run-trace — client for the deterministic, pollable studio run.
 *
 * POST /studio/run starts the real traced Phase-A spine in the background and returns
 * the run_id IMMEDIATELY; the spine writes per-role agent_runs as each agent lands.
 * GET /studio/run/{id} returns the steps so far + status, so the FE polls (~1.5s) and
 * renders each agent as it finishes — live progress, not a batch reveal.
 *
 * HONESTY: every step is a real model call persisted by the backend. Nothing is
 * fabricated here; on a transport error the caller surfaces it. NOTHING sends — the
 * run produces HELD/PENDING rows only.
 */

import type { CampaignBlueprint, ProgressBoard } from './blueprint';

export type RunStatus = 'running' | 'awaiting_selection' | 'completed' | 'error' | 'unknown';

/** One pickable artwork option in a paused run's selection request. */
export interface SelectionOption {
  assetId: string;
  artifactId: string;
  styles: string[];
  motifs: string[];
  why: string | null;
}

/**
 * A run PAUSED for an operator pick (status 'awaiting_selection'): the engine asks
 * a question and offers real artwork options. POST select-artwork resumes the run.
 */
export interface SelectionRequest {
  kind: string; // 'artwork'
  question: string;
  options: SelectionOption[];
}

/** One per-agent trace step (a row of agent_runs). */
export interface RunStep {
  seq: number;
  role: string;
  model: string | null;
  input: unknown;
  output: unknown;
  createdAt?: string;
}

/** One HELD draft (a PENDING `actions` row) produced by the run. Carries exactly
 *  what the result/review surface needs to render an Approve / Reject / Deep-Review
 *  card and drive the EXISTING approve mutation (id + idempotencyKey). Real-only. */
export interface PendingAction {
  id: string;
  channel: string | null;
  target: string | null;
  subject: string | null;
  draft: string;
  idempotencyKey: string;
  status: string;
}

/** One live narration line the engine derived from a REAL recorded step (host voice). */
export interface NarrationLine {
  seq: number;
  role: string;
  line: string;
  failed: boolean;
}

/** One row that was NOT drafted, with the exact reason (skipped) or failure. */
export interface ReconcileRow {
  row: number | null;
  lead: string | null;
  reason: string;
}

/**
 * Draft-count reconciliation (sgr): every requested row accounted for as created OR
 * skipped OR failed — with per-row reasons — so the operator (and voice) sees the same
 * count the review queue holds. Sourced from campaign_state.reconciliation (DB only).
 */
export interface Reconciliation {
  requested: number;
  expected: number;
  created: number;
  inQueue: number;
  approved: number;
  sent: number;
  rejected: number;
  skipped: ReconcileRow[];
  failed: ReconcileRow[];
  accounted: number;
  reconciled: boolean;
}

export interface RunState {
  runId: string;
  status: RunStatus;
  steps: RunStep[];
  /** Host-voice narration, one honest line per recorded step (engine-derived).
   *  Optional so existing RunState constructors (the empty starting state) still
   *  typecheck; fetchRunState always fills it from the polled response. */
  narration?: NarrationLine[];
  nPending: number | null;
  /** The real HELD draft rows for this run (empty until drafts stage). */
  pending: PendingAction[];
  archetype: string | null;
  /** P1.5: the planner's executable plan for this run (null on a pre-P1.5 run). */
  blueprint?: CampaignBlueprint | null;
  /** P1.5: the durable structured progress board for this run (null when none). */
  board?: ProgressBoard | null;
  /** sgr: draft-count reconciliation (requested vs created/in-queue/skipped/failed). */
  reconciliation?: Reconciliation | null;
  /** Present while status === 'awaiting_selection' — the operator must pick. */
  selectionRequest?: SelectionRequest | null;
  error: string | null;
}

/** Derive the `/studio` base from the configured AG-UI URL (strip trailing /agui). */
function studioBase(aguiUrl: string): string {
  return aguiUrl.replace(/\/agui(\?.*)?$/, '');
}

/** Start a run; resolves with the run_id once the backend has accepted it. */
export async function startRun(
  aguiUrl: string,
  sessionId: string,
  plan: unknown,
  signal?: AbortSignal,
): Promise<{ runId: string }> {
  const res = await fetch(`${studioBase(aguiUrl)}/run`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ sessionId, plan }),
    signal,
  });
  if (!res.ok) throw new Error(`studio run start HTTP ${res.status}`);
  const data = (await res.json()) as { ok?: boolean; runId?: string; error?: string };
  if (!data.ok || !data.runId) throw new Error(data.error ?? 'studio run start failed');
  return { runId: data.runId };
}

/** Fetch the current state of a run (steps so far + status). */
export async function fetchRunState(
  aguiUrl: string,
  runId: string,
  signal?: AbortSignal,
): Promise<RunState> {
  const res = await fetch(`${studioBase(aguiUrl)}/run/${encodeURIComponent(runId)}`, {
    method: 'GET',
    headers: { accept: 'application/json' },
    signal,
  });
  if (!res.ok) throw new Error(`studio run state HTTP ${res.status}`);
  const d = (await res.json()) as {
    runId: string;
    status?: RunStatus;
    steps?: RunStep[];
    narration?: NarrationLine[];
    nPending?: number | null;
    pending?: PendingAction[];
    archetype?: string | null;
    blueprint?: CampaignBlueprint | null;
    board?: ProgressBoard | null;
    // campaign_state block (voice/reconciliation live-state surface).
    state?: { reconciliation?: Reconciliation | null } | null;
    // Present when the run paused for an operator artwork pick (spec section 22).
    selection_request?: SelectionRequest | null;
    selectionRequest?: SelectionRequest | null;
    error?: string | null;
  };
  return {
    runId: d.runId ?? runId,
    status: d.status ?? 'unknown',
    steps: Array.isArray(d.steps) ? d.steps : [],
    narration: Array.isArray(d.narration) ? d.narration : [],
    nPending: d.nPending ?? null,
    pending: Array.isArray(d.pending) ? d.pending : [],
    archetype: d.archetype ?? null,
    blueprint: d.blueprint ?? null,
    board: d.board ?? null,
    // Reconciliation comes from the campaign_state block the endpoint attaches.
    reconciliation: d.state?.reconciliation ?? null,
    selectionRequest: parseSelectionRequest(d.selection_request ?? d.selectionRequest),
    error: d.error ?? null,
  };
}

/** Defensive parse of a selection_request payload (contract: kind/question/options). */
function parseSelectionRequest(raw: unknown): SelectionRequest | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const rawOptions = Array.isArray(r.options) ? (r.options as Array<Record<string, unknown>>) : [];
  const options: SelectionOption[] = rawOptions
    .map((o) => ({
      assetId: typeof o.assetId === 'string' ? o.assetId : '',
      artifactId: typeof o.artifactId === 'string' ? o.artifactId : '',
      styles: Array.isArray(o.styles) ? o.styles.filter((s): s is string => typeof s === 'string') : [],
      motifs: Array.isArray(o.motifs) ? o.motifs.filter((s): s is string => typeof s === 'string') : [],
      why: typeof o.why === 'string' && o.why.length > 0 ? o.why : null,
    }))
    .filter((o) => o.assetId.length > 0);
  if (options.length === 0) return null;
  return {
    kind: typeof r.kind === 'string' ? r.kind : 'artwork',
    question: typeof r.question === 'string' ? r.question : 'Pick an artwork for this campaign.',
    options,
  };
}

/**
 * Resume a run paused on an artwork pick: POST /studio/campaign/{runId}/select-artwork
 * with the chosen assetId. The caller keeps polling — the run resumes server-side.
 */
export async function selectArtwork(
  aguiUrl: string,
  runId: string,
  assetId: string,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(
    `${studioBase(aguiUrl)}/campaign/${encodeURIComponent(runId)}/select-artwork`,
    {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ assetId }),
      signal,
    },
  );
  if (!res.ok) throw new Error(`select-artwork HTTP ${res.status}`);
  const data = (await res.json().catch(() => ({}))) as { ok?: boolean; error?: string };
  if (data.ok === false) throw new Error(data.error ?? 'select-artwork failed');
}
