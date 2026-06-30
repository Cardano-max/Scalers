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

/** The host-voice narration for ONE recorded step (pure — reads only that step). */
export function narrationLine(step: RunStep): string {
  const role = String(step.role ?? '').trim().toLowerCase();
  const input = asRecord(step.input);
  const output = asRecord(step.output);
  const failed = stepFailed(step.output);
  const lead = leadLabel(input);
  const channel = String(input.channel ?? '').trim();

  if (role === 'strategist') {
    if (failed) return 'The strategist hit a snag setting the angle, so the team is drafting straight from your goal.';
    const angle = String(output.target_angle ?? output.angle ?? '').trim();
    return angle ? `The strategist set the campaign angle: “${angle}”.` : 'The strategist set the campaign angle for the team.';
  }
  if (role === 'researcher') {
    const who = lead || 'this lead';
    if (failed) return `Research on ${who} ran into trouble — continuing from what's already on file.`;
    if (output.degraded) return `Researching ${who} — no fresh web sources came back, so drafting from their record.`;
    return `Researching ${who} — pulling their history and profile.`;
  }
  if (role === 'draft') {
    const ch = channel ? `${channel} ` : '';
    return lead
      ? `The copywriter is drafting a personalized ${ch}message for ${lead}.`
      : `The copywriter is drafting a personalized ${ch}message.`;
  }
  if (role === 'critic') {
    const ch = channel ? `${channel} ` : '';
    if (failed) return `The critic couldn't finish its review on the ${ch}draft — flagged for you to check.`;
    const verdict = String(output.verdict ?? '').trim();
    const who = lead ? ` for ${lead}` : '';
    return verdict ? `The critic reviewed the ${ch}draft${who} — verdict: ${verdict}.` : `The critic is reviewing the ${ch}draft${who}.`;
  }
  if (role === 'jury') {
    const note = String(output.note ?? '').trim();
    return note ? `Wrapping up — ${note}` : 'Wrapping up — aggregating confidence across the drafts; everything is held for your approval.';
  }
  const label = role || 'the team';
  return `${label.charAt(0).toUpperCase()}${label.slice(1)} step ${failed ? 'failed' : 'completed'}.`;
}

/** Project the run's REAL steps into host-voice narration — one line per recorded step. */
export function runNarration(steps: RunStep[] | null | undefined): NarrationLine[] {
  return (steps ?? []).map((step, i) => ({
    seq: typeof step.seq === 'number' ? step.seq : i,
    role: String(step.role ?? ''),
    line: narrationLine(step),
    failed: stepFailed(step.output),
  }));
}
