/**
 * studio-history.ts — pull the persisted, LABELED studio transcript.
 *
 * The role brainstorm (funnel_architect → copywriter → critic → jury) runs inside
 * the backend `brainstorm_with_roles` tool and is persisted to `studio_chat_turns`
 * — those role contributions are NOT carried on the AG-UI event stream, so we read
 * them back via the existing GraphQL `studioChatHistory` query. The persisted
 * transcript (operator + host + the four role cells, ordered by seq) is the source
 * of truth for the thread; the AG-UI stream only adds the live, in-flight host turn
 * on top while a run is streaming.
 *
 * Each persisted role becomes a `ChatTurn` with an accurate label — never a generic
 * "agent" — so the brainstorm renders as the real, attributed multi-agent exchange.
 */
import type { ChatTurn, StudioRole } from '@/lib/data/studio-adapter';

export interface BackendChatTurn {
  id: string;
  sessionId: string;
  seq: number;
  role: string;
  text: string;
  model: string | null;
  createdAt: string;
}

/** Map a backend role string to a console StudioRole + human label. */
function mapRole(role: string): { role: StudioRole; label: string } {
  switch (role) {
    case 'operator':
      return { role: 'OPERATOR', label: 'You' };
    case 'host':
      return { role: 'SYSTEM', label: 'Studio Host' };
    case 'funnel_architect':
      return { role: 'STRATEGIST', label: 'Funnel Architect' };
    case 'copywriter':
      return { role: 'COPYWRITER', label: 'Copywriter' };
    case 'critic':
      return { role: 'CRITIC', label: 'Critic' };
    case 'jury':
      return { role: 'JURY', label: 'Jury' };
    // P3.x wired-run roles (Phase-A spine traces surfaced from a run_campaign).
    case 'researcher':
      return { role: 'RESEARCHER', label: 'Researcher' };
    case 'strategist':
      return { role: 'STRATEGIST', label: 'Strategist' };
    case 'draft':
      return { role: 'COPYWRITER', label: 'Draft' };
    // Pipeline roles the engine persists but the client convo must not treat as
    // "Studio" system bubbles (tlv.3): mapping them to pipeline roles keeps them
    // out of the operator/host center thread and in the reasoning rail.
    case 'planner':
      return { role: 'STRATEGIST', label: 'Planner' };
    case 'analyst':
      return { role: 'RESEARCHER', label: 'Analyst' };
    default:
      return { role: 'SYSTEM', label: role || 'Studio' };
  }
}

// ---------------------------------------------------------------------------
// Transcript sanitizer (tlv.3) — the defensive FE gate over PERSISTED rows.
//
// The live 672-turn history proved old rows carry internals the client must not
// see: role='thinking' chain-of-thought, "[planner] {…" raw JSON, 24x duplicate
// per-lead analyst rows, raw CellExecutionError text, and double-persisted
// operator turns. The engine now writes clean summaries for NEW runs; this pure
// pass makes OLD rows render professionally too. The raw data stays available in
// the Runs tab / GraphQL — this only shapes the conversation surface.
// ---------------------------------------------------------------------------

/** Legacy per-lead pipeline roles the old engine mirrored once PER LEAD. */
const PER_LEAD_ROLES = new Set(['researcher', 'analyst', 'draft', 'critic']);

const CELL_ERROR_RE =
  /CellExecutionError|ModelHTTPError|Traceback \(most recent|cell '.*' failed|verdict=error/i;

/** True for OLD-format rows ("[role] raw output_summary…") that predate the
 *  engine-side collapse — only these are eligible for spam-collapse/rewrite. */
function isLegacyRow(t: BackendChatTurn): boolean {
  return t.text.startsWith(`[${t.role}]`);
}

function stripRolePrefix(t: BackendChatTurn): string {
  return t.text.slice(`[${t.role}]`.length).replace(/^\s+/, '');
}

function collapseText(role: string, n: number, failed: number): string {
  const failNote = failed > 0 ? ` ${failed} step(s) hit an error — details in the Runs tab.` : '';
  switch (role) {
    case 'researcher':
      return `Researched ${n} leads — details in the Runs tab.${failNote}`;
    case 'analyst':
      return `Analyzed ${n} leads — categories and objections read from each lead's history; details in the Runs tab.${failNote}`;
    case 'draft':
      return `Drafted ${n} personalized messages — all held for your review.${failNote}`;
    case 'critic':
      return failed > 0
        ? `Reviewed ${n} drafts — ${n - failed} passed; ${failed} check(s) failed and are flagged for review (details in the Runs tab).`
        : `Reviewed ${n} drafts — details in the Runs tab.`;
    default:
      return `${n} ${role} steps completed — details in the Runs tab.${failNote}`;
  }
}

const PLAN_REVISED_RE =
  /^\[plan\] revised: goal=(['"])(.*?)\1 audience=(['"])(.*?)\3 channels=\[(.*?)\]\s*$/;

/** Rewrite OLD engine-authored host rows ("[plan] revised: goal='…'",
 *  "[generate] …") into the human lines the engine now writes directly. */
function rewriteLegacyHostRow(t: BackendChatTurn): BackendChatTurn {
  if (t.role !== 'host') return t;
  if (t.text.startsWith('[plan] revised:')) {
    const m = t.text.match(PLAN_REVISED_RE);
    const text = m
      ? `Updated the plan — goal: ${m[2]}; audience: ${m[4]}; channels: ${m[5].replace(/['"]/g, '')}.`
      : 'Updated the campaign plan.';
    return { ...t, text };
  }
  if (t.text.startsWith('[generate] ')) {
    return { ...t, text: t.text.slice('[generate] '.length) };
  }
  return t;
}

/** Rewrite ONE legacy row's text into a client-readable line (pure). */
function rewriteLegacyRow(t: BackendChatTurn): BackendChatTurn {
  let text = stripRolePrefix(t);
  if (t.role === 'planner' && text.startsWith('{')) {
    text = 'Planned the campaign — the full blueprint is in the Runs tab.';
  } else if (CELL_ERROR_RE.test(text)) {
    text = `The ${t.role} check failed on this step — flagged for review (details in the Runs tab).`;
  } else {
    text = text.replace(/\s·\s/g, ', ');
  }
  return text === t.text ? t : { ...t, text };
}

/**
 * Clean the persisted transcript for the client conversation (pure):
 *  1. drop role='thinking' rows (raw chain-of-thought is never client-facing);
 *  2. collapse consecutive LEGACY per-lead spam rows (same role, "[role] …"
 *     format) into one turn carrying the REAL row count;
 *  3. rewrite remaining legacy rows (strip "[role]" prefix, humanize raw planner
 *     JSON, turn raw cell errors into an honest one-line flag);
 *  4. dedupe adjacent identical turns (double-persisted operator rows).
 */
export function sanitizeBackendTurns(rows: BackendChatTurn[]): BackendChatTurn[] {
  const kept = rows.filter((r) => r.role !== 'thinking');

  // Collapse consecutive legacy per-lead rows of the same role.
  const collapsed: BackendChatTurn[] = [];
  let i = 0;
  while (i < kept.length) {
    const t = kept[i];
    if (PER_LEAD_ROLES.has(t.role) && isLegacyRow(t)) {
      let j = i;
      while (j < kept.length && kept[j].role === t.role && isLegacyRow(kept[j])) j += 1;
      const group = kept.slice(i, j);
      if (group.length >= 2) {
        const failed = group.filter((g) => CELL_ERROR_RE.test(g.text)).length;
        collapsed.push({ ...t, text: collapseText(t.role, group.length, failed) });
      } else {
        collapsed.push(rewriteLegacyRow(t));
      }
      i = j;
      continue;
    }
    collapsed.push(isLegacyRow(t) ? rewriteLegacyRow(t) : rewriteLegacyHostRow(t));
    i += 1;
  }

  // Dedupe adjacent identical turns (the double-persisted operator rows).
  return collapsed.filter(
    (t, idx) => idx === 0 || t.role !== collapsed[idx - 1].role || t.text !== collapsed[idx - 1].text,
  );
}

function toChatTurn(t: BackendChatTurn): ChatTurn {
  const { role, label } = mapRole(t.role);
  return { id: t.id, role, label, text: t.text, at: t.createdAt };
}

const STUDIO_CHAT_HISTORY = `query StudioChatHistory($sessionId: String!) {
  studioChatHistory(sessionId: $sessionId) { id sessionId seq role text model createdAt }
}`;

/**
 * Fetch the persisted transcript for a session, mapped to labeled ChatTurns.
 * Throws on transport failure so the caller can keep the honest preview state.
 */
export async function fetchStudioHistory(
  graphqlUrl: string,
  sessionId: string,
  signal?: AbortSignal,
): Promise<ChatTurn[]> {
  const res = await fetch(graphqlUrl, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query: STUDIO_CHAT_HISTORY, variables: { sessionId } }),
    signal,
  });
  if (!res.ok) throw new Error(`studio history HTTP ${res.status}`);
  const json = (await res.json()) as {
    data?: { studioChatHistory: BackendChatTurn[] };
    errors?: Array<{ message?: string }>;
  };
  if (json.errors?.length) throw new Error(json.errors[0]?.message ?? 'studio history error');
  return sanitizeBackendTurns(json.data?.studioChatHistory ?? []).map(toChatTurn);
}
