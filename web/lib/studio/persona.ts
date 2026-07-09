/**
 * persona.ts — visual identity for each speaker in the Campaign Studio chat.
 *
 * The studio thread mixes the operator with many distinct agents (Studio Host,
 * Strategist, Funnel Architect, Copywriter, the per-draft Drafts, Critics, Jury).
 * Several of these collapse onto the SAME coarse `StudioRole` in the data model
 * (e.g. `funnel_architect` and `strategist` are both STRATEGIST; every `draft`
 * and `critic` cell is COPYWRITER / CRITIC) — so colouring purely by `role` makes
 * genuinely different agents look identical. We resolve the persona from the
 * human LABEL first (which IS distinct per agent) and fall back to the role.
 *
 * This is the single source of truth for "who is this card and what colour is it".
 */
import type { StudioRole } from '@/lib/data/studio-adapter';

export interface StudioPersona {
  /** Stable key — used for data-attributes and tests. */
  key: string;
  /** Bold card title shown to the operator. */
  name: string;
  /** Primary accent (name text + avatar foreground + left rail). */
  accent: string;
  /** Soft card background. */
  bg: string;
  /** Card border. */
  border: string;
  /** 2-char avatar initials (never equal to `name`, so tests can target each). */
  initials: string;
  /** Layout side: the operator sits on the right, every agent on the left. */
  side: 'left' | 'right';
}

/** The operator — rendered as a right-aligned bubble, no avatar. */
export const OPERATOR_PERSONA: StudioPersona = {
  key: 'operator',
  name: 'You',
  accent: '#0F8A82',
  bg: '#E3F3F1',
  border: '#A6DAD4',
  initials: 'YOU',
  side: 'right',
};

/**
 * The agent palette. Distinct, readable accents per the studio team so the
 * conversation reads as an attributed multi-agent exchange, not a grey wall.
 */
export const AGENT_PERSONAS = {
  host: {
    key: 'host',
    name: 'Studio Host',
    accent: '#6D4AE6',
    bg: '#F2EEFE',
    border: '#D8CCFB',
    initials: 'SH',
    side: 'left',
  },
  strategist: {
    key: 'strategist',
    name: 'Strategist',
    accent: '#2563C9',
    bg: '#EAF1FC',
    border: '#BBD3F4',
    initials: 'St',
    side: 'left',
  },
  funnel: {
    key: 'funnel',
    name: 'Funnel Architect',
    accent: '#0B7E76',
    bg: '#E0F2F0',
    border: '#A4D8D2',
    initials: 'FA',
    side: 'left',
  },
  copywriter: {
    key: 'copywriter',
    name: 'Copywriter',
    accent: '#9A6B00',
    bg: '#FBF3E0',
    border: '#EBD49E',
    initials: 'Cw',
    side: 'left',
  },
  draft: {
    key: 'draft',
    name: 'Draft',
    accent: '#1A8F54',
    bg: '#E6F6EE',
    border: '#A7DEBF',
    initials: 'Dr',
    side: 'left',
  },
  critic: {
    key: 'critic',
    name: 'Critic',
    accent: '#C2362B',
    bg: '#FCEBE9',
    border: '#F1BEB8',
    initials: 'Cr',
    side: 'left',
  },
  jury: {
    key: 'jury',
    name: 'Jury',
    accent: '#4338CA',
    bg: '#ECEBFB',
    border: '#C6C1F2',
    initials: 'Ju',
    side: 'left',
  },
  researcher: {
    key: 'researcher',
    name: 'Researcher',
    accent: '#1D6FB8',
    bg: '#E7F1FA',
    border: '#B3D5EE',
    initials: 'Re',
    side: 'left',
  },
  planner: {
    key: 'planner',
    name: 'Planner',
    accent: '#7A5AF8',
    bg: '#F0EDFE',
    border: '#D3C9FB',
    initials: 'Pl',
    side: 'left',
  },
  safety: {
    key: 'safety',
    name: 'Safety',
    accent: '#B42318',
    bg: '#FCECEA',
    border: '#F2C0BA',
    initials: 'Sf',
    side: 'left',
  },
  system: {
    key: 'system',
    name: 'Studio',
    accent: '#6B6461',
    bg: '#F4F2EE',
    border: '#E2DED5',
    initials: '··',
    side: 'left',
  },
} as const satisfies Record<string, StudioPersona>;

/** Human label from a raw engine role: 'artist_memory' -> 'Artist memory'. */
export function humanizeRole(role: string): string {
  const cleaned = (role || '').trim().replace(/[_-]+/g, ' ').replace(/\s+/g, ' ');
  if (!cleaned) return 'Agent';
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1).toLowerCase();
}

/** Distinct, deterministic accents for roles OUTSIDE the fixed palette above, so a
 *  new crew (e.g. the IG-specific roles) still renders attributed and readable. */
const GENERATED_ACCENTS = [
  '#2563C9',
  '#0B7E76',
  '#9A6B00',
  '#B4531F',
  '#4338CA',
  '#1D6FB8',
  '#7A5AF8',
  '#8A6D3B',
];

function hashString(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/**
 * Build a persona for ANY engine role not in the fixed palette. Deterministic
 * (same role -> same accent), honestly labeled with the role's own name — this is
 * what lets the console render whatever crew the engine actually ran (planner /
 * artist_memory / trend_research / …) instead of a hardcoded agent list.
 */
export function generatedPersona(role: string): StudioPersona {
  const name = humanizeRole(role);
  const accent = GENERATED_ACCENTS[hashString(role.toLowerCase()) % GENERATED_ACCENTS.length];
  const words = name.split(' ').filter(Boolean);
  const initials =
    words.length >= 2
      ? (words[0][0] + words[1][0]).toUpperCase()
      : name.slice(0, 2).replace(/^\w/, (c) => c.toUpperCase());
  return {
    key: role.toLowerCase(),
    name,
    accent,
    bg: `${accent}14`,
    border: `${accent}40`,
    initials,
    side: 'left',
  };
}

/** Role fallback when the label is generic/unknown. */
function personaForRole(role: StudioRole): StudioPersona {
  switch (role) {
    case 'STRATEGIST':
      return AGENT_PERSONAS.strategist;
    case 'COPYWRITER':
      return AGENT_PERSONAS.copywriter;
    case 'CRITIC':
      return AGENT_PERSONAS.critic;
    case 'JURY':
      return AGENT_PERSONAS.jury;
    case 'RESEARCHER':
      return AGENT_PERSONAS.researcher;
    case 'SAFETY':
      return AGENT_PERSONAS.safety;
    case 'SYSTEM':
    default:
      return AGENT_PERSONAS.system;
  }
}

/**
 * Resolve the persona for a chat turn. Label-first (distinct per agent), then
 * role. The operator is always the right-aligned "You" persona.
 */
export function studioPersona(turn: { role: StudioRole; label?: string }): StudioPersona {
  if (turn.role === 'OPERATOR') return OPERATOR_PERSONA;

  const label = (turn.label ?? '').toLowerCase();
  if (label.includes('host')) return AGENT_PERSONAS.host;
  if (label.includes('funnel')) return AGENT_PERSONAS.funnel;
  if (label.includes('strateg')) return AGENT_PERSONAS.strategist;
  if (label.includes('copywriter') || label.includes('copy')) return AGENT_PERSONAS.copywriter;
  if (label.includes('draft')) return AGENT_PERSONAS.draft;
  if (label.includes('critic')) return AGENT_PERSONAS.critic;
  if (label.includes('jury')) return AGENT_PERSONAS.jury;
  if (label.includes('research')) return AGENT_PERSONAS.researcher;
  if (label.includes('safety')) return AGENT_PERSONAS.safety;

  return personaForRole(turn.role);
}
