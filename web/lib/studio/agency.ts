/**
 * agency.ts — the REAL-run → agency-narrative mapping for the Agency-at-Work view.
 *
 * Every label, count, and active flag here derives from the real RunState.steps
 * (agent_runs rows written by engine campaign_runner.py: researcher / strategist /
 * draft×N / critic×N / jury). Nothing is invented: counts are real `length`s, the
 * active stage is the single earliest expected stage that has not landed a step,
 * and the evidence text is the real step.output. Honest empty states everywhere.
 */
import { AGENT_PERSONAS, type StudioPersona } from './persona';
import type { RunState, RunStep } from './run-trace';

export type AgencyStageKey = 'research' | 'strategy' | 'drafts' | 'critics' | 'jury';

export interface AgencyStage {
  key: AgencyStageKey;
  /** The cinematic narrative label the operator reads. */
  label: string;
  /** Short verb shown while the stage is in flight. */
  verb: string;
  persona: StudioPersona;
  accent: string;
  /** Real count of landed steps for this stage. */
  count: number;
  /** Whether the ×N fan-out badge is meaningful (drafts / critics). */
  countable: boolean;
  done: boolean;
  /** The single earliest expected stage with no landed step, while running. */
  active: boolean;
  /** ISO createdAt of this stage's FIRST landed step (gates the handoff edge draw). */
  firstCreatedAt: string | null;
  /** The real steps that belong to this stage, in seq order. */
  steps: RunStep[];
}

const STAGE_DEFS: {
  key: AgencyStageKey;
  roles: string[];
  label: string;
  verb: string;
  persona: StudioPersona;
  countable: boolean;
}[] = [
  { key: 'research', roles: ['researcher'], label: 'Deep research', verb: 'researching the web', persona: AGENT_PERSONAS.researcher, countable: true },
  { key: 'strategy', roles: ['strategist'], label: 'Strategist planning', verb: 'setting the angle', persona: AGENT_PERSONAS.strategist, countable: false },
  { key: 'drafts', roles: ['draft', 'copywriter'], label: 'Copywriters drafting', verb: 'writing drafts', persona: AGENT_PERSONAS.draft, countable: true },
  { key: 'critics', roles: ['critic'], label: 'Critics re-verifying', verb: 're-verifying each draft', persona: AGENT_PERSONAS.critic, countable: true },
  { key: 'jury', roles: ['jury'], label: 'Supervising jury evaluating', verb: 'evaluating the work', persona: AGENT_PERSONAS.jury, countable: false },
];

/** Map a raw agent_runs role to its persona (for roster + evidence cards). */
export function personaForRunRole(role: string): StudioPersona {
  const r = (role || '').toLowerCase();
  if (r.includes('research')) return AGENT_PERSONAS.researcher;
  if (r.includes('strateg')) return AGENT_PERSONAS.strategist;
  if (r.includes('draft') || r.includes('copy')) return AGENT_PERSONAS.draft;
  if (r.includes('critic')) return AGENT_PERSONAS.critic;
  if (r.includes('jury')) return AGENT_PERSONAS.jury;
  if (r.includes('safety')) return AGENT_PERSONAS.safety;
  return AGENT_PERSONAS.system;
}

export function deriveAgencyStages(runState: RunState | null, running = false): AgencyStage[] {
  const steps = runState?.steps ?? [];
  const stages: AgencyStage[] = STAGE_DEFS.map((def) => {
    const mine = steps
      .filter((s) => def.roles.includes((s.role || '').toLowerCase()))
      .sort((a, b) => a.seq - b.seq);
    const firstCreatedAt = mine.find((s) => s.createdAt)?.createdAt ?? null;
    return {
      key: def.key,
      label: def.label,
      verb: def.verb,
      persona: def.persona,
      accent: def.persona.accent,
      count: mine.length,
      countable: def.countable,
      done: mine.length > 0,
      active: false,
      firstCreatedAt,
      steps: mine,
    };
  });

  if (running) {
    // The active stage is the earliest expected stage that has not landed a step.
    const next = stages.find((s) => !s.done);
    if (next) next.active = true;
  }

  return stages;
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
