/**
 * narration — client mirror of the engine's live team narration (engine/studio/agui.py
 * run_narration). While a run executes, the supervisor narrates progress from the REAL
 * recorded agent_runs (the run's steps), one honest host-voice line per step.
 *
 * The engine surfaces the same lines on GET /studio/run/{id} as the `narration` field;
 * this mirror lets the FE render the narration directly from the steps it already polls,
 * and keeps the projection unit-tested without a backend. HONESTY: the timeline IS the
 * data — we never narrate a stage that did not actually run, never invent a lead, and a
 * failed strategist / critic step is narrated as a snag, not as success.
 */
import type { RunStep } from './run-trace';

export interface NarrationLine {
  seq: number;
  role: string;
  line: string;
  failed: boolean;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function stepFailed(output: unknown): boolean {
  const out = asRecord(output);
  const status = String(out.status ?? '').toLowerCase();
  if (status === 'failed' || status === 'error') return true;
  const verdict = String(out.verdict ?? '').toLowerCase();
  return verdict === 'error' || verdict === 'failed';
}

function leadLabel(input: Record<string, unknown>): string {
  const name = input.name ?? input.lead ?? input.customer_id;
  return name ? String(name).trim() : '';
}

// Per-lead roles whose narration carries an "X of N" progress tag when N is known.
const PER_LEAD_ROLES = new Set(['researcher', 'analyst', 'draft', 'critic']);

/** The REAL planned lead count for this run, read from the recorded steps (the
 *  strategist / jury record n_leads). 0 when none carries it, so "X of N" is dropped
 *  only when the total is genuinely unknown — never fabricated. */
function plannedLeadTotal(steps: RunStep[]): number {
  for (const s of steps) {
    const n = asRecord(s.input).n_leads;
    if (typeof n === 'number' && Number.isInteger(n) && n > 0) return n;
  }
  return 0;
}

/** The host-voice narration for ONE recorded step (pure — reads only that step plus an
 *  already-computed REAL `progress` tag like "3 of 10"). */
export function narrationLine(step: RunStep, progress = ''): string {
  const role = String(step.role ?? '').trim().toLowerCase();
  const input = asRecord(step.input);
  const output = asRecord(step.output);
  const failed = stepFailed(step.output);
  const lead = leadLabel(input);
  const channel = String(input.channel ?? '').trim();
  const prog = progress ? ` (${progress})` : '';

  if (role === 'strategist') {
    if (failed) return 'The strategist hit a snag setting the angle, so the team is drafting straight from your goal.';
    const angle = String(output.target_angle ?? output.angle ?? '').trim();
    return angle ? `The strategist set the campaign angle: “${angle}”.` : 'The strategist set the campaign angle for the team.';
  }
  if (role === 'analyst') {
    const who = lead || 'this lead';
    if (failed) return `Reading ${who}'s history${prog} hit a snag — continuing from what's on file.`;
    const cat = String(output.umbrella_category ?? '').replace(/-/g, ' ').trim();
    const obj = String(output.primary_objection ?? '').trim();
    if (obj && obj !== 'none-found') return `Analyzing ${who}${prog} — ${cat ? `${cat}, ` : ''}reading their objection: ${obj}.`;
    return `Analyzing ${who}${prog} — reading where they sit${cat ? `: ${cat}` : ''}.`;
  }
  if (role === 'researcher') {
    const who = lead || 'this lead';
    if (failed) return `Research on ${who}${prog} ran into trouble — continuing from what's already on file.`;
    if (output.degraded) return `Researching ${who}${prog} — no fresh web sources came back, so drafting from their record.`;
    return `Researching ${who}${prog} — pulling their history and profile.`;
  }
  if (role === 'draft') {
    const ch = channel ? `${channel} ` : '';
    const base = lead
      ? `The copywriter is drafting a personalized ${ch}message for ${lead}`
      : `The copywriter is drafting a personalized ${ch}message`;
    return `${base}${prog}.`;
  }
  if (role === 'critic') {
    const ch = channel ? `${channel} ` : '';
    const who = lead ? ` for ${lead}` : '';
    if (failed) return `The critic couldn't finish its review on the ${ch}draft${who}${prog} — flagged for you to check.`;
    const verdict = String(output.verdict ?? '').trim();
    return verdict
      ? `The critic reviewed the ${ch}draft${who}${prog} — verdict: ${verdict}.`
      : `The critic is reviewing the ${ch}draft${who}${prog}.`;
  }
  if (role === 'jury') {
    const note = String(output.note ?? '').trim();
    return note ? `Wrapping up — ${note}` : 'Wrapping up — aggregating confidence across the drafts; everything is held for your approval.';
  }
  const label = role || 'the team';
  return `${label.charAt(0).toUpperCase()}${label.slice(1)} step ${failed ? 'failed' : 'completed'}.`;
}

/** Project the run's REAL steps into host-voice narration — one line per recorded step.
 *  Per-lead steps carry an "X of N" tag when the planned total N is genuinely known
 *  from the run data (the recorded n_leads); X is the real count of that role done so far. */
export function runNarration(steps: RunStep[] | null | undefined): NarrationLine[] {
  const list = (steps ?? []).filter((s): s is RunStep => !!s && typeof s === 'object');
  const total = plannedLeadTotal(list);
  const roleDone: Record<string, number> = {};
  return list.map((step, i) => {
    const role = String(step.role ?? '').trim().toLowerCase();
    let progress = '';
    if (PER_LEAD_ROLES.has(role)) {
      roleDone[role] = (roleDone[role] ?? 0) + 1;
      if (total) progress = `${roleDone[role]} of ${total}`;
    }
    return {
      seq: typeof step.seq === 'number' ? step.seq : i,
      role: String(step.role ?? ''),
      line: narrationLine(step, progress),
      failed: stepFailed(step.output),
    };
  });
}
