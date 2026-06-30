'use client';

/**
 * LineageChips — the minimal, clickable identity row shown wherever a draft /
 * activity item / run step appears. Each chip deep-links to the EXACT related
 * item by stable id (never the first one), so the console feels like one linked
 * system:
 *   Campaign → its run     Run → the run + step
 *   Agent (role) → the producing step's reasoning      Action → the queue draft
 *   Created (time) and Channel are plain context labels.
 *
 * HONESTY: a chip is a LINK only when its id genuinely resolves to a real target.
 * A blank/sentinel id ('', '  ', 'null', 'undefined') is treated as absent — we
 * never render a chip that labels a campaign/run that does not exist or links
 * nowhere. The campaign chip is clickable only when we also have the real run it
 * opens; when a real run carries NO campaign we show an explicit, honest
 * "no campaign" state instead of omitting it or faking a label. The agent chip
 * links to the producing step's reasoning when we know the step; if we only know
 * the run, it links to the run and says so — never a guessed wrong step.
 */
import { type CSSProperties, type ReactNode } from 'react';
import { useConsole } from '@/state/console-store';
import { clockTime } from '../console-bits';

export interface Lineage {
  /** Real campaign id (derived run_id → runs/agent_runs). */
  campaignId?: string | null;
  /** The owning workflow run id. */
  runId?: string | null;
  /** Producing agent role, e.g. Strategist / Copywriter / Critic / Jury. */
  agentRole?: string | null;
  /** The action/draft id (Review-queue + Activity row). */
  actionId?: string | null;
  /** ISO creation time. */
  createdAt?: string | null;
  /** Output channel label. */
  channel?: string | null;
  /** Run-level Langfuse trace url (per-step span ids are not persisted). */
  traceUrl?: string | null;
}

const PILL: CSSProperties = {
  fontFamily: "'IBM Plex Mono', monospace",
  fontSize: 10,
  padding: '2px 7px',
  borderRadius: 5,
  whiteSpace: 'nowrap',
  maxWidth: 240,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  display: 'inline-flex',
  alignItems: 'center',
  gap: 4,
};

const CLICKABLE: CSSProperties = {
  ...PILL,
  color: '#0B6F68',
  background: '#EAF4F2',
  border: '1px solid #C9E5E1',
  cursor: 'pointer',
};

const PLAIN: CSSProperties = {
  ...PILL,
  color: '#8C877D',
  background: 'rgba(0,0,0,0.02)',
  border: '1px solid #E8E5DE',
  cursor: 'default',
};

const AGENT: CSSProperties = {
  ...PILL,
  color: '#4B4640',
  background: '#F2EFE9',
  border: '1px solid #E0DCD3',
  cursor: 'pointer',
};

/**
 * A real id resolves only when it is a non-blank string that is not a stringified
 * null/undefined sentinel. Anything else is treated as absent so we never render a
 * chip labelling a campaign/run that does not exist (e.g. `campaign:null`) or that
 * would link nowhere.
 */
function hasId(value: string | null | undefined): value is string {
  if (typeof value !== 'string') return false;
  const trimmed = value.trim();
  if (trimmed.length === 0) return false;
  const lowered = trimmed.toLowerCase();
  return lowered !== 'null' && lowered !== 'undefined';
}

function Pill({
  label,
  title,
  style,
  onClick,
}: {
  label: string;
  title: string;
  style: CSSProperties;
  onClick?: () => void;
}) {
  if (!onClick) {
    return (
      <span title={title} style={style}>
        {label}
      </span>
    );
  }
  return (
    <span
      role="button"
      tabIndex={0}
      title={title}
      style={style}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          e.stopPropagation();
          onClick();
        }
      }}
    >
      {label}
    </span>
  );
}

export function LineageChips({
  lineage,
  size = 'sm',
}: {
  lineage: Lineage;
  size?: 'sm' | 'md';
}) {
  const console = useConsole();
  const { campaignId, runId, agentRole, actionId, createdAt, channel, traceUrl } = lineage;

  const chips: ReactNode[] = [];

  // Campaign chip. It is a LINK only when we have BOTH a real campaign id AND the
  // real run it opens (the only campaign navigation in this console). A real run
  // that carries no campaign shows an explicit honest "no campaign" pill rather
  // than silently dropping the chip or faking a label that resolves nowhere.
  if (hasId(campaignId) && hasId(runId)) {
    chips.push(
      <Pill
        key="campaign"
        label={`campaign:${campaignId}`}
        title={`Open the run for campaign ${campaignId}`}
        style={CLICKABLE}
        onClick={() => console.navigate('runs', runId)}
      />,
    );
  } else if (hasId(campaignId)) {
    // Real campaign id but no run to open: a plain, non-clickable context label
    // (it states the id honestly; it is deliberately not a dead link).
    chips.push(
      <Pill key="campaign" label={`campaign:${campaignId}`} title={`Campaign ${campaignId}`} style={PLAIN} />,
    );
  } else if (hasId(runId)) {
    chips.push(
      <Pill key="campaign" label="no campaign" title="This run is not tied to a campaign" style={PLAIN} />,
    );
  }

  if (hasId(runId)) {
    chips.push(
      <Pill
        key="run"
        label={`run:${runId}`}
        title={`Open run ${runId}`}
        style={CLICKABLE}
        onClick={() => console.navigate('runs', runId)}
      />,
    );
  }

  if (agentRole) {
    // The agent chip opens the producing step's reasoning when we can resolve a
    // step (we key off the action id, which Step detail resolves); otherwise it
    // falls back to the run — never a guessed wrong step.
    const target = hasId(actionId)
      ? () => console.navigate('step_detail', actionId)
      : hasId(runId)
        ? () => console.navigate('runs', runId)
        : undefined;
    chips.push(
      <Pill
        key="agent"
        label={agentRole}
        title={
          hasId(actionId)
            ? `Open ${agentRole}'s reasoning for this item`
            : hasId(runId)
              ? `Open the run (exact ${agentRole} step not linked)`
              : `Produced by ${agentRole}`
        }
        style={target ? AGENT : PLAIN}
        onClick={target}
      />,
    );
  }

  if (hasId(actionId)) {
    chips.push(
      <Pill
        key="action"
        label={`action:${actionId}`}
        title={`Open this item in Activity (${actionId})`}
        style={CLICKABLE}
        onClick={() => console.navigate('activity', actionId)}
      />,
    );
  }

  if (createdAt) {
    chips.push(
      <Pill key="created" label={clockTime(createdAt)} title={createdAt} style={PLAIN} />,
    );
  }

  if (channel) {
    chips.push(<Pill key="channel" label={channel} title={`Channel ${channel}`} style={PLAIN} />);
  }

  if (traceUrl) {
    chips.push(
      <Pill
        key="trace"
        label="trace ↗"
        title="Open the run-level Langfuse trace (per-step spans not persisted)"
        style={CLICKABLE}
        onClick={() => window.open(traceUrl, '_blank')}
      />,
    );
  }

  if (chips.length === 0) return null;

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        flexWrap: 'wrap',
        fontSize: size === 'md' ? 11 : 10,
      }}
    >
      {chips}
    </div>
  );
}
