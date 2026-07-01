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

export type RunStatus = 'running' | 'completed' | 'error' | 'unknown';

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
    error: d.error ?? null,
  };
}
