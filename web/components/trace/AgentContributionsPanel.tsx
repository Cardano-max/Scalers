'use client';

/**
 * Agent contributions panel — WHY this draft took a team, not one prompt.
 *
 * For the selected draft it renders, per agent (strategy, research, identity
 * guardian, location resolver, analyst, copywriter, critic, jury): what the
 * agent was FOR, the concrete output it produced for THIS lead, and how the
 * next agent consumed it. Bound to GET /studio/action/{id}/contributions,
 * which assembles everything from the run's recorded agent_runs trail — the
 * panel never narrates a step that was not recorded.
 *
 * Honesty contract: a degraded/idle/missing step SAYS so (e.g. "no public
 * candidates found — nothing to vet", "location unknown — not invented") in
 * a muted tone; no entry is invented and none is hidden to look better.
 */
import { useData } from '@/lib/data/DataProvider';
import { useAsync } from '@/lib/useAsync';
import { Chip } from '../console-bits';
import type { ActionContributions, AgentContribution } from '@/lib/data/models';

const STATUS_TONE: Record<string, 'success' | 'amber' | 'neutral'> = {
  done: 'success',
  degraded: 'amber',
  missing: 'neutral',
  idle: 'neutral',
};

function EvidenceList({ evidence }: { evidence: string[] | string }) {
  if (typeof evidence === 'string') {
    return (
      <div style={{ fontSize: 11.5, color: 'var(--text-secondary-2)', fontStyle: 'italic' }}>
        “{evidence}”
      </div>
    );
  }
  if (!evidence.length) return null;
  return (
    <div style={{ display: 'grid', gap: 2 }}>
      {evidence.slice(0, 4).map((e) =>
        /^https?:\/\//.test(e) ? (
          <a
            key={e}
            href={e}
            target="_blank"
            rel="noreferrer"
            className="mono"
            style={{ fontSize: 11, color: 'var(--accent-dark, var(--accent))', overflowWrap: 'anywhere' }}
          >
            {e}
          </a>
        ) : (
          <div key={e} style={{ fontSize: 11.5, color: 'var(--text-secondary-2)', fontStyle: 'italic' }}>
            “{e}”
          </div>
        ),
      )}
    </div>
  );
}

function ContributionRow({ c }: { c: AgentContribution }) {
  const muted = c.status !== 'done';
  return (
    <div
      data-testid={`contribution-${c.agent.toLowerCase().replace(/\s+/g, '-')}`}
      style={{
        display: 'grid',
        gap: 4,
        padding: '9px 0',
        borderTop: '1px solid var(--hairline)',
        opacity: muted ? 0.85 : 1,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12.5, fontWeight: 700 }}>{c.agent}</span>
        {c.model ? (
          <span className="mono" style={{ fontSize: 10.5, color: 'var(--text-muted)' }}>
            {c.model}
          </span>
        ) : null}
        <Chip tone={STATUS_TONE[c.status] ?? 'neutral'} style={{ marginLeft: 'auto' }}>
          {c.status}
        </Chip>
        {c.personalization ? (
          <Chip tone={c.personalization.level === 'high' ? 'success' : 'neutral'}>
            personalization: {c.personalization.level}
          </Chip>
        ) : null}
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>{c.purpose}</div>
      <div style={{ fontSize: 12.5, overflowWrap: 'anywhere' }}>{c.output}</div>
      {c.evidence ? <EvidenceList evidence={c.evidence} /> : null}
      {c.rationale ? (
        <div style={{ fontSize: 11.5, color: 'var(--text-secondary-2)' }}>{c.rationale}</div>
      ) : null}
      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>→ {c.nextUse}</div>
    </div>
  );
}

export function AgentContributionsPanel({ actionId }: { actionId: string }) {
  const { adapter } = useData();
  const { data, loading } = useAsync<ActionContributions | null>(
    // Tolerate partial fake adapters (older tests): missing method reads the
    // same as "no trail recorded".
    () =>
      typeof adapter.getActionContributions === 'function'
        ? adapter.getActionContributions(actionId)
        : Promise.resolve(null),
    [adapter, actionId],
  );

  if (loading) return null; // render once resolved, like the other panels
  if (!data || data.contributions.length === 0) return null; // no trail — no theater

  return (
    <section
      aria-label="Agent contributions"
      data-testid="agent-contributions"
      style={{
        border: '1px solid var(--hairline)',
        borderRadius: 'var(--radius-card)',
        background: 'var(--surface)',
        padding: '12px 14px',
        display: 'grid',
        gap: 4,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <div style={{ fontSize: 12.5, fontWeight: 700 }}>
          Agent contributions — how this draft was actually built
        </div>
        <span className="mono" style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-muted)' }}>
          {data.agentRunCount} recorded agent steps in this run
        </span>
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>{data.note}</div>
      <div style={{ display: 'grid' }}>
        {data.contributions.map((c) => (
          <ContributionRow key={c.agent} c={c} />
        ))}
      </div>
    </section>
  );
}
