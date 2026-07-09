/**
 * agency.ts — the REAL-run → agency-narrative mapping for the Agency-at-Work view.
 *
 * ROLE-DRIVEN, NOT HARDCODED: the lanes/roster render EXACTLY the roles that appear
 * in the real RunState.steps (agent_runs rows) — planner / researcher / strategist /
 * draft / critic / jury when those ran, or the IG-specific crew (artist_memory /
 * trend_research / …) when THOSE ran. A role the engine never emitted never renders;
 * a role we've never heard of still renders (honest generated persona + its own
 * name). Counts are real lengths, models are the real per-step models, and the
 * active flag derives from the run status + the latest step's completeness.
 */
import {
  AGENT_PERSONAS,
  generatedPersona,
  humanizeRole,
  type StudioPersona,
} from './persona';
import type { RunState, RunStep } from './run-trace';

/**
 * An HONEST per-agent status derived from REAL run state. A landed-but-failed step
 * must read 'failed', never a fake 'done'; a role with an in-flight (no-output-yet)
 * step reads 'running' while the run is live. Roles that never appear in steps[]
 * simply don't render — there is no fabricated "queued" lane.
 */
export type AgentRunStatus =
  | 'done'
  | 'running'
  | 'waiting-for-prev'
  | 'skipped-not-required'
  | 'failed'
  | 'blocked-missing-input'
  | 'cancelled';

/** Short lane/roster label for each honest status. */
export const AGENT_STATUS_LABEL: Record<AgentRunStatus, string> = {
  done: 'done',
  running: 'running',
  'waiting-for-prev': 'waiting',
  'skipped-not-required': 'skipped',
  failed: 'failed',
  'blocked-missing-input': 'blocked',
  cancelled: 'cancelled',
};

/** Hover/title text explaining each honest status. */
export const AGENT_STATUS_TITLE: Record<AgentRunStatus, string> = {
  done: 'Completed',
  running: 'Running now',
  'waiting-for-prev': 'Waiting for the previous step',
  'skipped-not-required': 'Not required for this campaign',
  failed: 'Failed',
  'blocked-missing-input': 'Blocked — a required input never arrived',
  cancelled: 'Cancelled',
};

export interface AgencyStage {
  /** Normalized role key (the REAL agent_runs role, lowercased). */
  key: string;
  /** The narrative label the operator reads (role's own name). */
  label: string;
  /** Short verb shown while the stage is in flight. */
  verb: string;
  persona: StudioPersona;
  accent: string;
  /** Real count of landed steps for this role. */
  count: number;
  /** Whether the ×N fan-out badge is meaningful (real fan-out only). */
  countable: boolean;
  done: boolean;
  /** True when this role's latest step is still in flight during a live run. */
  active: boolean;
  /** Kept for API compat; role-driven stages never fabricate a skipped lane. */
  skipped: boolean;
  /** The honest status of this agent, derived from REAL run state. */
  status: AgentRunStatus;
  /** The real model of this role's latest landed step (null when unrecorded). */
  model: string | null;
  /** ISO createdAt of this role's FIRST landed step (gates the handoff edge draw). */
  firstCreatedAt: string | null;
  /** The real steps that belong to this role, in seq order. */
  steps: RunStep[];
}

/** Operator-vocabulary narrative for the well-known roles; anything else uses its
 *  own (humanized) role name — display labels only, never a reason to invent a lane. */
const ROLE_NARRATIVE: Record<string, { label: string; verb: string }> = {
  planner: { label: 'Planner sequencing', verb: 'sequencing the plan' },
  researcher: { label: 'Deep research', verb: 'researching the web' },
  strategist: { label: 'Strategist planning', verb: 'setting the angle' },
  draft: { label: 'Copywriters drafting', verb: 'writing drafts' },
  copywriter: { label: 'Copywriters drafting', verb: 'writing drafts' },
  critic: { label: 'Critics re-verifying', verb: 're-verifying each draft' },
  jury: { label: 'Supervising jury evaluating', verb: 'evaluating the work' },
};

function narrativeFor(roleKey: string): { label: string; verb: string } {
  return ROLE_NARRATIVE[roleKey] ?? { label: humanizeRole(roleKey), verb: 'working' };
}

/** Map a raw agent_runs role to its persona. Known roles keep the fixed palette;
 *  unknown roles get an honest, deterministic generated persona (their own name). */
export function personaForRunRole(role: string): StudioPersona {
  const r = (role || '').toLowerCase().trim();
  if (!r) return AGENT_PERSONAS.system;
  // Exact-first so e.g. 'trend_research' does NOT collapse onto Researcher.
  if (r === 'researcher' || r === 'research') return AGENT_PERSONAS.researcher;
  if (r === 'strategist') return AGENT_PERSONAS.strategist;
  if (r === 'planner') return AGENT_PERSONAS.planner;
  if (r === 'draft') return AGENT_PERSONAS.draft;
  if (r === 'copywriter') return AGENT_PERSONAS.copywriter;
  if (r === 'critic') return AGENT_PERSONAS.critic;
  if (r === 'jury') return AGENT_PERSONAS.jury;
  if (r === 'safety') return AGENT_PERSONAS.safety;
  if (r === 'host' || r === 'system') return AGENT_PERSONAS.system;
  return generatedPersona(r);
}

/**
 * True when a landed step is an HONEST failure rather than a success. The provided-leads
 * path records a failed cell as a real step (so the lane keeps its lineage and the run
 * continues) marked in its output: the strategist writes `status='failed'` and the critic
 * writes `verdict='error'`. A landed-but-failed stage must read 'failed', NOT 'done' — a
 * 429/rate-limited critic showing "done" would misreport a fake success. Real success
 * outputs (strategy fields, a real approve/revise/reject verdict, researcher/draft/jury
 * payloads) carry neither marker, so they stay 'done'.
 */
function stepFailed(step: RunStep): boolean {
  const o = step.output;
  if (!o || typeof o !== 'object') return false;
  const r = o as Record<string, unknown>;
  return r['status'] === 'failed' || r['verdict'] === 'error';
}

/** A landed row with no output yet — the engine wrote the step when the agent
 *  STARTED; it is the honest "running now" signal while the run is live. */
function stepIncomplete(step: RunStep): boolean {
  return step.output == null || step.output === '';
}

/**
 * Derive the per-agent lanes from the REAL steps: one stage per DISTINCT role that
 * actually appears, in order of first appearance (seq). No expected-pipeline
 * skeleton, no channel assumptions — an email run shows the email crew, an IG run
 * shows the IG crew, because the lanes ARE the recorded roles.
 */
export function deriveAgencyStages(runState: RunState | null, running = false): AgencyStage[] {
  const steps = (runState?.steps ?? []).slice().sort((a, b) => a.seq - b.seq);
  if (steps.length === 0) return [];

  const order: string[] = [];
  const groups = new Map<string, RunStep[]>();
  for (const s of steps) {
    const key = (s.role || 'agent').toLowerCase().trim() || 'agent';
    if (!groups.has(key)) {
      groups.set(key, []);
      order.push(key);
    }
    groups.get(key)!.push(s);
  }

  return order.map((key) => {
    const mine = groups.get(key)!;
    const persona = personaForRunRole(key);
    const { label, verb } = narrativeFor(key);
    const hasFailed = mine.some(stepFailed);
    const last = mine[mine.length - 1];
    const isRunning = running && !hasFailed && stepIncomplete(last);
    const status: AgentRunStatus = hasFailed ? 'failed' : isRunning ? 'running' : 'done';
    return {
      key,
      label,
      verb,
      persona,
      accent: persona.accent,
      count: mine.length,
      countable: mine.length > 1,
      done: status === 'done',
      active: isRunning,
      skipped: false,
      status,
      model: last.model ?? null,
      firstCreatedAt: mine.find((s) => s.createdAt)?.createdAt ?? null,
      steps: mine,
    };
  });
}

/** Extract a readable one-line summary from a real step output (no fabrication —
 *  unknown shapes fall back to a JSON slice). */
export function stepSummaryLine(step: RunStep): string {
  const o = step.output;
  if (o && typeof o === 'object') {
    const r = o as Record<string, unknown>;
    const pick = (k: string) => (typeof r[k] === 'string' ? (r[k] as string) : '');
    const hook = pick('hook') || pick('headline');
    const cta = pick('call_to_action') || pick('cta');
    if (hook || cta) return [hook && `Hook: ${hook}`, cta && `CTA: ${cta}`].filter(Boolean).join(' · ');
    const verdict = pick('verdict');
    const conf = r['confidence'];
    if (verdict) return `Verdict: ${verdict}${typeof conf === 'number' ? ` · confidence ${conf}` : ''}`;
    const angle = pick('primary_angle') || pick('angle') || pick('big_idea') || pick('primary_conversion');
    if (angle) return angle;
    const decision = pick('decision') || pick('aggregate');
    if (decision) return decision;
    try {
      return JSON.stringify(o).slice(0, 220);
    } catch {
      return '';
    }
  }
  return o == null ? '' : String(o).slice(0, 220);
}

export interface ResearchSource {
  url: string;
  title?: string | null;
  snippet?: string | null;
  query?: string | null;
}

/** Pull REAL research sources out of a researcher step's output if present
 *  (sources / citations / research_sources arrays of {url,title,snippet}). Returns
 *  [] when none — the rail then shows the honest gated/empty state, never a fake. */
export function extractResearchSources(steps: RunStep[]): ResearchSource[] {
  const out: ResearchSource[] = [];
  for (const s of steps) {
    const o = s.output;
    if (!o || typeof o !== 'object') continue;
    const r = o as Record<string, unknown>;
    const arr =
      (Array.isArray(r['sources']) && r['sources']) ||
      (Array.isArray(r['citations']) && r['citations']) ||
      (Array.isArray(r['research_sources']) && r['research_sources']) ||
      null;
    if (!arr) continue;
    for (const item of arr as unknown[]) {
      if (item && typeof item === 'object') {
        const it = item as Record<string, unknown>;
        const url = typeof it['url'] === 'string' ? (it['url'] as string) : '';
        if (!url) continue;
        out.push({
          url,
          title: typeof it['title'] === 'string' ? (it['title'] as string) : null,
          snippet: typeof it['snippet'] === 'string' ? (it['snippet'] as string) : null,
          query: typeof it['query'] === 'string' ? (it['query'] as string) : null,
        });
      } else if (typeof item === 'string' && /^https?:\/\//.test(item)) {
        out.push({ url: item });
      }
    }
  }
  return out;
}

/** domain for a URL (favicon + label), defensive against malformed URLs. */
export function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url.slice(0, 40);
  }
}

/** Human-readable duration between two ISO timestamps (e.g. "1.8s", "420ms"). */
export function durationBetween(from: string | null | undefined, to: string | null | undefined): string | null {
  if (!from || !to) return null;
  const a = Date.parse(from);
  const b = Date.parse(to);
  if (Number.isNaN(a) || Number.isNaN(b) || b < a) return null;
  const ms = b - a;
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}
