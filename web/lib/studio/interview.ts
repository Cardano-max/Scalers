/**
 * interview — client + pure gate for the Agency-page scoping interview (P1a).
 *
 * The Agency page must NOT launch a run blindly. The supervisor interviews the
 * operator first (text, voice, or upload) and the Run button stays LOCKED until
 * enough context is gathered. The authority is the engine's pure state machine
 * (engine/studio/interview.py, surfaced at POST /studio/interview): this module
 * speaks to it AND mirrors its arming rule client-side (deriveInterview) so the
 * panel can render an honest gate before the first round-trip and so the rule is
 * unit-tested without a backend.
 *
 * HONESTY: the server decision wins. deriveInterview is only the optimistic local
 * mirror used for the initial render + the disabled/enabled Run button; every
 * answer is a real POST whose returned state replaces the local guess.
 */
import type { CampaignPlan } from './agui';

export interface InterviewQuestion {
  field: string;
  question: string;
}

/** One step the engine has planned for this run, with WHY it is (or is not) running.
 *  `selected` steps execute; the rest are skipped. `tools` are the tools a selected
 *  step will use. Surfaced so the operator sees the plan before the run starts. */
export interface PlannedStep {
  id: string;
  label: string;
  selected: boolean;
  reason: string;
  tools: string[];
}

export interface InterviewState {
  armed: boolean;
  missing: string[];
  collected: Record<string, unknown>;
  nextQuestion: InterviewQuestion | null;
  readyMessage: string | null;
  gatingFields: string[];
  // Dynamic step selection (P4). Optional so older responses still typecheck.
  mode?: string;
  modeLabel?: string;
  plannedSteps?: PlannedStep[];
}

/** How the panel renders the input for each field (mirrors engine coercion). */
export type FieldKind = 'text' | 'number' | 'list' | 'yesno' | 'drafts_or_stage' | 'lead_source';

export interface FieldMeta {
  field: string;
  question: string;
  kind: FieldKind;
  /** Short label for the collected-chips row. */
  label: string;
}

// Gating fields — ALL must be answered before the run can arm. Order = ask order.
export const GATING_META: FieldMeta[] = [
  { field: 'goal', label: 'Goal', kind: 'text', question: "What's the goal of this campaign?" },
  { field: 'audience', label: 'Audience', kind: 'text', question: "Who's the target audience?" },
  { field: 'channels', label: 'Channels', kind: 'list', question: 'Which channels? (email, instagram, facebook, sms)' },
  {
    field: 'lead_source',
    label: 'Lead source',
    kind: 'lead_source',
    question: 'Lead source: source NEW leads from the web, or use ONLY your uploaded CSV / existing database leads?',
  },
  { field: 'campaign_type', label: 'Type', kind: 'text', question: 'What type? (win-back, artist-spotlight, promo, event, birthday)' },
  { field: 'output_count', label: 'Drafts', kind: 'number', question: 'How many drafts/outputs should the team produce?' },
];

// Optional fields — asked after gating, never block the run.
export const OPTIONAL_META: FieldMeta[] = [
  { field: 'action_type', label: 'Action', kind: 'text', question: 'Outreach, posts, replies, or comments?' },
  { field: 'deep_research', label: 'Deep research', kind: 'yesno', question: 'Run deep web research first?' },
  { field: 'lead_count', label: 'Leads', kind: 'number', question: 'How many leads to target? (0 if not a leads campaign)' },
  { field: 'tone', label: 'Tone', kind: 'text', question: 'Any tone or brand-voice notes?' },
  { field: 'drafts_only', label: 'Disposition', kind: 'drafts_or_stage', question: 'Drafts only, or stage for your approval?' },
];

export const ALL_META: FieldMeta[] = [...GATING_META, ...OPTIONAL_META];
export const GATING_FIELDS = GATING_META.map((m) => m.field);

const LIST_FIELDS = new Set(['channels']);
const INT_FIELDS = new Set(['output_count', 'lead_count']);
const BOOL_FIELDS = new Set(['deep_research', 'drafts_only']);

/** Whether a field carries a real operator answer (mirrors engine field_present). */
export function fieldPresent(plan: Partial<CampaignPlan>, field: string): boolean {
  const val = (plan as Record<string, unknown>)[field];
  if (LIST_FIELDS.has(field)) return Array.isArray(val) && val.some((c) => String(c).trim());
  if (INT_FIELDS.has(field)) return typeof val === 'number' && val > 0;
  if (BOOL_FIELDS.has(field)) return val === true || val === false;
  return typeof val === 'string' && val.trim().length > 0;
}

/** The local mirror of the engine gate — used for the initial render + the Run-button
 *  enabled state. The server response is authoritative and replaces this. */
export function deriveInterview(plan: Partial<CampaignPlan>): InterviewState {
  const missing = GATING_FIELDS.filter((f) => !fieldPresent(plan, f));
  const armed = missing.length === 0;
  const next = ALL_META.find((m) => !fieldPresent(plan, m.field)) ?? null;
  const collected: Record<string, unknown> = {};
  for (const m of ALL_META) collected[m.field] = (plan as Record<string, unknown>)[m.field];
  return {
    armed,
    missing,
    collected,
    nextQuestion: next ? { field: next.field, question: next.question } : null,
    readyMessage: armed
      ? "I have enough context. Say 'go ahead' or click Run to start the team — everything stays HELD for your approval."
      : null,
    gatingFields: GATING_FIELDS,
  };
}

/** Derive the `/studio` base from the configured AG-UI URL (strip trailing /agui). */
function studioBase(aguiUrl: string): string {
  return aguiUrl.replace(/\/agui(\?.*)?$/, '');
}

export interface InterviewResponse {
  ok: boolean;
  plan: CampaignPlan;
  armed: boolean;
  missing: string[];
  collected: Record<string, unknown>;
  nextQuestion: InterviewQuestion | null;
  readyMessage: string | null;
  gatingFields: string[];
  // Dynamic step selection (P4) — optional so older responses still typecheck.
  mode?: string;
  modeLabel?: string;
  plannedSteps?: PlannedStep[];
  error?: string;
}

/**
 * POST one or more interview answers (or none, to just read the current gate state).
 * Returns the authoritative plan + gate state from the engine. Throws on transport
 * failure so the caller degrades to the honest not-connected state.
 */
export async function postInterview(
  aguiUrl: string,
  sessionId: string,
  fields: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<InterviewResponse> {
  const res = await fetch(`${studioBase(aguiUrl)}/interview`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ sessionId, fields }),
    signal,
  });
  if (!res.ok) throw new Error(`studio interview HTTP ${res.status}`);
  return (await res.json()) as InterviewResponse;
}
