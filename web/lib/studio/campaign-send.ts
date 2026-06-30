/**
 * campaign-send — thin client for the campaign-level SAFE-SEND surface.
 *
 * The engine classifies a run's staged drafts into the ones that clear the safety
 * bar (eligible) and the ones that do NOT (review_required), and exposes two send
 * paths: a one-click send of ONLY the eligible drafts, and an explicit, audited
 * per-draft override for a held one. Every send still flows through the EXISTING
 * approve path server-side; nothing here invents a send.
 *
 * HONESTY: classify is READ-ONLY (sends nothing). send-eligible touches ONLY the
 * eligible set. override is the single way to push a held draft past the bar and it
 * is recorded as an audit entry server-side. On a non-2xx we surface the engine's
 * own `error` message verbatim so the operator sees WHY, never a fake success.
 *
 * The routes are reached same-origin through the existing `/studio/*` Next rewrite,
 * so these are plain relative fetches (no base needed). Snake_case from the engine
 * is kept as-is in the TS types to stay 1:1 with the JSON.
 */

/** One staged draft as the engine classifies it (snake_case, 1:1 with the JSON). */
export interface CampaignDraft {
  action_id: string;
  run_id: string;
  channel: string | null;
  target: string | null;
  worker: string | null;
  conf: number | null;
  threshold: number | null;
  esc_kind: string | null;
  eligible: boolean;
  reason: string;
}

/** The split of a run's staged drafts into eligible vs review-required. */
export interface CampaignClassification {
  run_id: string;
  eligible: CampaignDraft[];
  review_required: CampaignDraft[];
  n_eligible: number;
  n_review_required: number;
}

/** One row in a send result bucket. Shape is loose: the engine echoes the action id
 *  plus, on failure/skip, a reason or provider error. We only render the counts. */
export interface SendItem {
  action_id?: string;
  reason?: string;
  error?: string;
  [k: string]: unknown;
}

/** Result of sending ONLY the eligible drafts. */
export interface SendEligibleResult {
  sent: SendItem[];
  failed: SendItem[];
  skipped: SendItem[];
  n_sent: number;
  n_failed: number;
  n_skipped: number;
}

/** Result of an audited per-draft override. `ok:false` carries the engine error. */
export interface OverrideResult {
  ok: boolean;
  action_id: string;
  was_eligible: boolean;
  eligibility_reason: string;
  result: unknown;
  last_error: string | null;
}

/** Pull the engine's verbatim `error` from a non-2xx body, falling back to status. */
async function errorFrom(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { error?: string };
    if (body && typeof body.error === 'string' && body.error.trim()) return body.error;
  } catch {
    /* no JSON body — fall through to the status line */
  }
  return `studio campaign HTTP ${res.status}`;
}

/** READ-ONLY: classify a run's staged drafts into eligible vs review-required. */
export async function classifyCampaign(
  runId: string,
  signal?: AbortSignal,
): Promise<CampaignClassification> {
  const res = await fetch(`/studio/campaign/${encodeURIComponent(runId)}/classify`, {
    method: 'GET',
    headers: { accept: 'application/json' },
    signal,
  });
  if (!res.ok) throw new Error(await errorFrom(res));
  const d = (await res.json()) as Partial<CampaignClassification>;
  const eligible = Array.isArray(d.eligible) ? d.eligible : [];
  const reviewRequired = Array.isArray(d.review_required) ? d.review_required : [];
  return {
    run_id: d.run_id ?? runId,
    eligible,
    review_required: reviewRequired,
    n_eligible: d.n_eligible ?? eligible.length,
    n_review_required: d.n_review_required ?? reviewRequired.length,
  };
}

/** Send ONLY the eligible drafts, each through the existing approve path. */
export async function sendEligible(
  runId: string,
  operator?: string,
): Promise<SendEligibleResult> {
  const res = await fetch(`/studio/campaign/${encodeURIComponent(runId)}/send-eligible`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(operator ? { operator } : {}),
  });
  if (!res.ok) throw new Error(await errorFrom(res));
  const d = (await res.json()) as Partial<SendEligibleResult>;
  const sent = Array.isArray(d.sent) ? d.sent : [];
  const failed = Array.isArray(d.failed) ? d.failed : [];
  const skipped = Array.isArray(d.skipped) ? d.skipped : [];
  return {
    sent,
    failed,
    skipped,
    n_sent: d.n_sent ?? sent.length,
    n_failed: d.n_failed ?? failed.length,
    n_skipped: d.n_skipped ?? skipped.length,
  };
}

/** Audited override: push ONE held draft past the safety bar. `reason` is required;
 *  the engine returns HTTP 400 with an `error` if it is empty (we surface that). */
export async function overrideSend(
  actionId: string,
  reason: string,
  operator?: string,
): Promise<OverrideResult> {
  const res = await fetch(`/studio/campaign/action/${encodeURIComponent(actionId)}/override`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(operator ? { reason, operator } : { reason }),
  });
  if (!res.ok) throw new Error(await errorFrom(res));
  const d = (await res.json()) as Partial<OverrideResult>;
  return {
    ok: d.ok ?? false,
    action_id: d.action_id ?? actionId,
    was_eligible: d.was_eligible ?? false,
    eligibility_reason: d.eligibility_reason ?? '',
    result: d.result ?? null,
    last_error: d.last_error ?? null,
  };
}
