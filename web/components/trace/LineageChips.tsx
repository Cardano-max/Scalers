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
 * HONESTY: a chip renders ONLY when its id genuinely exists. A missing field is
 * omitted, never faked. The agent chip links to the producing step's reasoning
 * when we know the step; if we only know the run, it links to the run and says
 * so in its tooltip — we never point it at a guessed wrong step.
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

  if (campaignId) {
    chips.push(
      <Pill
        key="campaign"
        label={`campaign:${campaignId}`}
        title={runId ? `Open the run for campaign ${campaignId}` : `Campaign ${campaignId}`}
        style={runId ? CLICKABLE : PLAIN}
        onClick={runId ? () => console.navigate('runs', runId) : undefined}
      />,
    );
  }

  if (runId) {
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
    const target = actionId
      ? () => console.navigate('step_detail', actionId)
      : runId
        ? () => console.navigate('runs', runId)
        : undefined;
    chips.push(
      <Pill
        key="agent"
        label={agentRole}
        title={
          actionId
            ? `Open ${agentRole}'s reasoning for this item`
            : runId
              ? `Open the run (exact ${agentRole} step not linked)`
              : `Produced by ${agentRole}`
        }
        style={target ? AGENT : PLAIN}
        onClick={target}
      />,
    );
  }

  if (actionId) {
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
