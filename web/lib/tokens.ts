/**
 * TS mirror of the load-bearing design tokens — the color maps the data layer
 * needs at runtime (channel dots, worker colors, semantic accents) so a
 * component can color a dot from a `Channel` / `Worker` enum value without
 * hard-coding hex. Visual tokens (neutrals, radius, shadow, type) live in
 * `app/globals.css`; this file only holds what code branches on.
 *
 * teal = automation / healthy ; amber = human-in-the-loop / escalated.
 */
import type { Channel, Worker, Severity, AutonomyMode } from './data/models';

/** Per-channel dot color (handoff "Channel dots"). Includes the live campaign
 *  channels the engine emits verbatim (EMAIL/SMS/IG/REELS/TIKTOK). */
export const CHANNEL_COLOR: Record<Channel, string> = {
  GMAIL: '#0F8A82',
  EMAIL: '#0F8A82',
  SMS: '#0B6F68',
  INSTAGRAM: '#7A5AF8',
  IG: '#7A5AF8',
  REELS: '#9333EA',
  TIKTOK: '#111111',
  FACEBOOK: '#2563C9',
};

/** Per-worker label color (handoff "Worker colors"); unmapped workers are muted. */
export const WORKER_COLOR: Record<Worker, string> = {
  OUTREACH: '#0F8A82',
  MAILBOX_MCP: '#0F8A82',
  PUBLISHER: '#7A5AF8',
  META_MCP: '#7A5AF8',
  RESPONDER: '#2563C9',
  JURY: '#9A6B00',
  SAFETY: '#B42318',
  CLASSIFIER: '#8C877D',
  WEBHOOK: '#8C877D',
  TEMPORAL: '#8C877D',
  RESEARCH: '#8C877D',
  STRATEGIST: '#7A5AF8',
  COPYWRITER: '#9A6B00',
  // Multi-agent campaign run workers.
  TEAM: '#0B6F68',
  DRAFT: '#9A6B00',
  CRITIC: '#B42318',
};

/** Feed/toast severity → semantic token group. */
export const SEVERITY_COLOR: Record<
  Severity,
  { text: string; bg: string; dot: string }
> = {
  INFO: { text: '#5C584F', bg: '#FBFAF7', dot: '#8C877D' },
  SUCCESS: { text: '#157F4B', bg: '#E6F4EC', dot: '#1A9D5E' },
  WARN: { text: '#9A6B00', bg: '#FBF0D9', dot: '#D99405' },
  ERROR: { text: '#B42318', bg: '#FBE9E6', dot: '#E04A38' },
};

/**
 * Autonomy chip semantics. AUTO renders teal ("Auto"); APPROVE_FIRST renders
 * amber ("You approved" / human-in-the-loop). The console only ever DISPLAYS
 * this — it never flips a channel to AUTO while the 439 HOLD is active.
 */
export const AUTONOMY_LABEL: Record<AutonomyMode, string> = {
  AUTO: 'Auto',
  APPROVE_FIRST: 'You approved',
};
