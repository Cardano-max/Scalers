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

/** One line of the senior-exec plan summary the operator approves before the go-ahead.
 *  `value` is already rendered from REAL state by the engine; the panel only displays it. */
export interface PlanSummaryLine {
  label: string;
  value: string;
}

/** The plan summary the supervisor reads back before running — built from REAL state
 *  (real uploaded lead count, real output count, real chosen channels). Null until the
 *  gate is armed, so the operator is never shown a go-ahead for a half-answered brief. */
export interface PlanSummary {
  title: string;
  goal: string;
  lines: PlanSummaryLine[];
  leadCount: number;
  channels: string[];
  confirm: string;
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
  // The senior-exec plan summary shown before the go-ahead. Optional/null = not armed.
  planSummary?: PlanSummary | null;
}

/** How the panel renders the input for each field (mirrors engine coercion). */
export type FieldKind =
  | 'text'
  | 'number'
  | 'list'
  | 'yesno'
  | 'drafts_or_stage'
  | 'lead_source'
  | 'per_lead_or_shared';

export interface FieldMeta {
  field: string;
  question: string;
  kind: FieldKind;
  /** Short label for the collected-chips row. */
  label: string;
}

// Gating fields — ALL must be answered before the run can arm. Order = ask order.
// Phrasing mirrors engine/studio/interview.py: plain, client-friendly language a
// senior agency exec would use with a non-technical operator (no bare jargon).
export const GATING_META: FieldMeta[] = [
  {
    field: 'goal',
    label: 'Goal',
    kind: 'text',
    question:
      "First — what are you hoping this campaign actually does for you? (e.g. fill quiet Tuesdays, win back clients you haven't seen in a while)",
  },
  {
    field: 'audience',
    label: 'Audience',
    kind: 'text',
    question:
      'Who exactly are we trying to reach? (e.g. your past clients, your Instagram followers, folks nearby)',
  },
  {
    field: 'channels',
    label: 'Channels',
    kind: 'list',
    question: 'How should we reach them — email, Instagram, Facebook, text message, or a mix?',
  },
  {
    field: 'lead_source',
    label: 'Lead source',
    kind: 'lead_source',
    question:
      "Should I reach out to ONLY the people on the list you've uploaded, or should I also go find brand-new prospects online?",
  },
  {
    field: 'campaign_type',
    label: 'Type',
    kind: 'text',
    question:
      'What kind of campaign is this — winning back old clients, spotlighting an artist, a promo or sale, an event, or a birthday note?',
  },
  {
    field: 'output_count',
    label: 'How many',
    kind: 'number',
    question: 'Roughly how many people should this go out to — how many messages should the team create?',
  },
  {
    field: 'offer',
    label: 'The ask',
    kind: 'text',
    question: "What's the offer or the ask? (e.g. a booking link, a discount code, or just 'reply to book')",
  },
];

// Optional fields — asked after gating, never block the run; each has a sensible default.
export const OPTIONAL_META: FieldMeta[] = [
  {
    field: 'per_lead',
    label: 'Per lead',
    kind: 'per_lead_or_shared',
    question: 'Should each person get their own personalized message, or one shared message for everyone?',
  },
  {
    field: 'personalize',
    label: 'Personalize',
    kind: 'yesno',
    question: "Want me to use each person's history and social profiles to tailor their message?",
  },
  { field: 'deep_research', label: 'Deep research', kind: 'yesno', question: 'Should I dig into each lead with deeper web research first?' },
  { field: 'tone', label: 'Tone', kind: 'text', question: 'Any particular tone — warm, playful, professional? (or leave it to your brand voice)' },
  { field: 'action_type', label: 'Action', kind: 'text', question: 'What should the team produce — outreach, posts, replies, or comments?' },
  { field: 'lead_count', label: 'Leads', kind: 'number', question: "How many leads to target? (0 if this isn't a leads campaign)" },
  { field: 'drafts_only', label: 'Disposition', kind: 'drafts_or_stage', question: 'Just write drafts, or stage them in your Review Queue for approval?' },
];

export const ALL_META: FieldMeta[] = [...GATING_META, ...OPTIONAL_META];
export const GATING_FIELDS = GATING_META.map((m) => m.field);

const LIST_FIELDS = new Set(['channels']);
const INT_FIELDS = new Set(['output_count', 'lead_count']);
const BOOL_FIELDS = new Set(['deep_research', 'drafts_only', 'personalize', 'per_lead']);

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
      ? "Great — I have everything I need. Here's the plan below. Have a quick look, and when it's right, say 'go ahead' (or click Run) and the team gets started. Nothing is sent — every message is held in your Review Queue for your approval."
      : null,
    gatingFields: GATING_FIELDS,
    // The authoritative summary comes from the engine; the local mirror leaves it null.
    planSummary: null,
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
  planSummary?: PlanSummary | null;
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
