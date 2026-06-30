'use client';

/**
 * Activity (handoff screen 3) — the auto-executed / approved work the agents
 * actually did, and WHY. Same master/detail shape as the review queue but for
 * COMPLETED actions: each row carries an outcome chip + an autonomy chip
 * (teal "Auto" / amber "You approved"); the detail shows engagement tiles, the
 * teal-tinted AGENT REASONING trace, the content that was sent/published, and a
 * "View conversation" / "View N comments" expander.
 *
 * Reads `getActivity` through the active adapter (mock or live, no code change).
 * Only the active screen mounts, so selection/expander state resets on nav.
 */
import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react';
import { useData } from '@/lib/data/DataProvider';
import { useAsync } from '@/lib/useAsync';
import { useConsole } from '@/state/console-store';
import { AsyncBoundary } from './states';
import { Dot } from './icons';
import { Chip, ProviderErrorPanel, Tag, actionIntent, channelLabel, clockTime, matchesFilter, typeLabel, type ChipTone, type QueueFilter } from './console-bits';
import { AUTONOMY_LABEL, CHANNEL_COLOR, WORKER_COLOR } from '@/lib/tokens';
import type { Action, ActivityItem, AutonomyMode } from '@/lib/data/models';
import { ExecutionTraceCard } from './trace/ExecutionTraceCard';
import { JuryCard } from './trace/JuryCard';

const FILTERS: Array<{ id: QueueFilter; label: string }> = [
  { id: 'ALL', label: 'All' },
  { id: 'OUTREACH', label: 'Outreach' },
  { id: 'REPLIES', label: 'Replies' },
  { id: 'POSTS', label: 'Posts' },
];

const OUTCOME_TONE: Record<ActivityItem['outcome']['kind'], ChipTone> = {
  success: 'success',
  teal: 'teal',
  neutral: 'neutral',
};

/** A pending Review-queue draft is still "what the campaign produced", so it
 *  surfaces in Activity too — honestly labeled as STAGED (not executed). We carry
 *  the real Action core through verbatim and add only truthful staged metadata:
 *  no fabricated engagement, reasoning, trace, or outcome. */
function stagedActivityFromAction(a: Action): ActivityItem {
  return {
    ...a,
    autonomy: 'APPROVE_FIRST',
    content: a.draft,
    outcome: { label: 'Staged · pending approval', kind: 'neutral' },
    thinking: [],
    engagement: [],
    thread: [],
    comments: [],
    runId: null,
    trace: null,
    judges: a.judges ?? [],
    spans: [],
    links: [],
  };
}

/** Staged drafts (awaiting approval) vs. executed work (sent/failed). */
function isStaged(item: ActivityItem): boolean {
  return item.status === 'PENDING' || item.status === 'APPROVED';
}

export function ActivityScreen() {
  const { adapter, tenantId } = useData();
  const console = useConsole();
  const activity = useAsync<ActivityItem[]>(() => adapter.getActivity(tenantId), [tenantId]);

  // Route the campaign's pending DRAFTS into Activity too (operator ask: drafts
  // go to Review queue + Activity + Live feed). Only on the LIVE source — the
  // mock spine keeps its own curated executed seed. Staged drafts render after the
  // executed work, clearly badged "Staged · pending approval"; nothing is sent.
  const isLive = adapter.source === 'live';
  const staged = useAsync<ActivityItem[]>(
    () =>
      isLive
        ? adapter.getReviewQueue(tenantId).then((as) => as.map(stagedActivityFromAction))
        : Promise.resolve([]),
    [tenantId, isLive],
  );

  const [filter, setFilter] = useState<QueueFilter>('ALL');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [threadOpen, setThreadOpen] = useState(false);

  // Executed first, then staged drafts — preserves the executed order the mock
  // spine (and its tests) rely on; no re-sort.
  const items = useMemo(
    () => [...(activity.data ?? []), ...(staged.data ?? [])],
    [activity.data, staged.data],
  );
  const ready = activity.data !== undefined && staged.data !== undefined;
  const loading = activity.loading || staged.loading;
  const error = activity.error ?? staged.error;
  const reload = () => {
    activity.reload();
    staged.reload();
  };
  const filtered = useMemo(() => items.filter((a) => matchesFilter(a.type, filter)), [items, filter]);
  const counts = useMemo(() => countByFilter(items), [items]);

  // Auto-select based on contextId from navigation
  useEffect(() => {
    if (console.contextId && filtered.some((a) => a.id === console.contextId)) {
      setSelectedId(console.contextId);
      console.setContext(null);
    }
  }, [console.contextId, filtered, console]);

  useEffect(() => {
    if (filtered.length === 0) {
      if (selectedId !== null) setSelectedId(null);
      return;
    }
    if (!selectedId || !filtered.some((a) => a.id === selectedId)) {
      setSelectedId(filtered[0].id);
    }
  }, [filtered, selectedId]);

  // The expander has one open-state per selected item (resets on row change).
  useEffect(() => {
    setThreadOpen(false);
  }, [selectedId]);

  const selected = filtered.find((a) => a.id === selectedId) ?? null;

  return (
    <div style={{ display: 'flex', height: '100%', minHeight: 0 }}>
      {/* ---------- LIST ---------- */}
      <div
        style={{
          width: 360,
          minWidth: 360,
          borderRight: '1px solid var(--hairline)',
          background: 'var(--surface)',
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
        }}
      >
        <div style={{ padding: '14px 16px 10px', borderBottom: '1px solid var(--hairline-light)' }}>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {FILTERS.map((f) => {
              const active = filter === f.id;
              return (
                <button
                  key={f.id}
                  type="button"
                  onClick={() => setFilter(f.id)}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                    border: `1px solid ${active ? 'var(--accent)' : 'var(--hairline)'}`,
                    background: active ? 'var(--nav-active-bg)' : 'var(--surface)',
                    color: active ? 'var(--accent-dark)' : 'var(--text-secondary)',
                    borderRadius: 'var(--radius-pill)',
                    padding: '4px 10px',
                    fontSize: 12,
                    fontWeight: active ? 600 : 500,
                    cursor: 'pointer',
                  }}
                >
                  {f.label}
                  <span className="mono" style={{ fontSize: 10.5, color: active ? 'var(--accent-dark)' : 'var(--text-muted)' }}>
                    {counts[f.id]}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
          <AsyncBoundary
            loading={loading}
            error={error}
            data={ready ? items : undefined}
            empty={filtered.length === 0}
            onRetry={reload}
            emptyTitle="No activity yet"
            emptyHint="Executed work and staged drafts land here with the agent’s reasoning."
          >
            {() => (
              <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                {filtered.map((a) => (
                  <ActivityRow
                    key={a.id}
                    item={a}
                    selected={a.id === selectedId}
                    onSelect={() => setSelectedId(a.id)}
                  />
                ))}
              </ul>
            )}
          </AsyncBoundary>
        </div>
      </div>

      {/* ---------- DETAIL ---------- */}
      <div style={{ flex: 1, overflow: 'auto', minWidth: 0, minHeight: 0 }}>
        {selected ? (
          <ActivityDetail item={selected} threadOpen={threadOpen} onToggleThread={() => setThreadOpen((v) => !v)} />
        ) : (
          <div style={{ padding: 'var(--pad-section)', color: 'var(--text-muted)', textAlign: 'center', marginTop: 40 }}>
            <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-secondary)' }}>No activity yet</div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------- list row ---------------- */

function ActivityRow({
  item,
  selected,
  onSelect,
}: {
  item: ActivityItem;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        style={{
          width: '100%',
          textAlign: 'left',
          font: 'inherit',
          cursor: 'pointer',
          display: 'block',
          position: 'relative',
          padding: '12px 16px 12px 18px',
          border: 'none',
          borderBottom: '1px solid var(--hairline-lighter)',
          background: selected ? 'var(--nav-active-bg)' : 'transparent',
          boxShadow: selected ? 'var(--shadow-selected)' : undefined,
        }}
      >
        <span
          aria-hidden
          style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 3, background: selected ? 'var(--accent)' : 'transparent' }}
        />
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          <Tag>{typeLabel(item.type)}</Tag>
          <Dot color={CHANNEL_COLOR[item.channel]} size={7} />
          {item.status === 'FAILED' ? (
            <Chip tone="danger" style={{ marginLeft: 'auto' }}>Failed</Chip>
          ) : isStaged(item) ? (
            <Chip tone="amber" style={{ marginLeft: 'auto' }}>Staged</Chip>
          ) : (
            <Chip tone={OUTCOME_TONE[item.outcome.kind]} style={{ marginLeft: 'auto' }}>
              {item.outcome.label}
            </Chip>
          )}
        </div>
        <div
          style={{ fontSize: 13, color: 'var(--ink)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
        >
          {item.subject ?? item.content}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 7 }}>
          <span className="mono" style={{ fontSize: 11, color: WORKER_COLOR[item.worker] }}>{item.worker}</span>
          {isStaged(item) ? (
            <Chip tone="neutral" style={{ marginLeft: 'auto' }}>Awaiting approval</Chip>
          ) : (
            <AutonomyChip mode={item.autonomy} style={{ marginLeft: 'auto' }} />
          )}
          <span className="mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>{clockTime(item.createdAt)}</span>
        </div>
      </button>
    </li>
  );
}

/* ---------------- detail ---------------- */

function ActivityDetail({
  item,
  threadOpen,
  onToggleThread,
}: {
  item: ActivityItem;
  threadOpen: boolean;
  onToggleThread: () => void;
}) {
  const console = useConsole();
  const hasThread = !!item.thread && item.thread.length > 0;
  const hasComments = !!item.comments && item.comments.length > 0;
  const expandLabel = hasComments ? `View ${item.comments!.length} comments` : 'View conversation';
  const isFailed = item.status === 'FAILED';
  const staged = isStaged(item);

  return (
    <div style={{ padding: 'var(--pad-section)', maxWidth: 1100, marginInline: 'auto', display: 'grid', gap: 18 }}>
      {/* header */}
      <div style={{ display: 'grid', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 17, fontWeight: 600 }}>{typeLabel(item.type)}</span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--text-secondary)' }}>
            <Dot color={CHANNEL_COLOR[item.channel]} size={8} />
            {channelLabel(item.channel)}
          </span>
          {staged ? null : <AutonomyChip mode={item.autonomy} />}
          <span className="mono" style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-muted)' }}>{clockTime(item.createdAt)}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 13.5, color: 'var(--text-secondary-2)' }}>{item.target}</span>
          {isFailed ? (
            <Chip tone="danger">Failed</Chip>
          ) : staged ? (
            <Chip tone="amber">Staged · pending approval</Chip>
          ) : (
            <Chip tone={OUTCOME_TONE[item.outcome.kind]}>{item.outcome.label}</Chip>
          )}
        </div>
      </div>

      {/* Staged draft: plain-language intent + HELD note. Nothing is sent; the
          operator approves/rejects in the Review queue. */}
      {staged ? (
        <div
          style={{
            border: '1px solid var(--reasoning-border)',
            borderRadius: 'var(--radius-card)',
            background: 'var(--reasoning-bg)',
            padding: '12px 14px',
            display: 'grid',
            gap: 4,
          }}
        >
          <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--reasoning-text)' }}>
            {actionIntent(item.type, item.channel, item.target)}
          </span>
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            Held for approval — not sent. Approve or reject this in the Review queue.
          </span>
        </div>
      ) : null}

      {/* FAILED send — surface the REAL provider error, never a bare "Failed". */}
      {isFailed && item.lastError ? <ProviderErrorPanel error={item.lastError} /> : null}

      {/* engagement tiles */}
      {item.engagement.length > 0 ? (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          {item.engagement.map((t) => (
            <div
              key={t.label}
              style={{
                flex: '1 1 120px',
                minWidth: 110,
                border: '1px solid var(--hairline)',
                borderRadius: 'var(--radius-card)',
                background: 'var(--surface)',
                boxShadow: 'var(--shadow-card)',
                padding: '12px 14px',
              }}
            >
              <div className="label" style={{ fontSize: 9.5 }}>{t.label}</div>
              <div style={{ fontSize: 17, fontWeight: 600, marginTop: 3 }}>{t.value}</div>
            </div>
          ))}
        </div>
      ) : null}

      {/* LINKS row + Open full trace */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {item.links && item.links.map((link, i) => (
          <button
            key={i}
            type="button"
            onClick={() => {
              if (link.target) window.open(link.target, '_blank');
            }}
            style={{
              fontSize: 12.5,
              fontWeight: 500,
              color: 'var(--accent-dark)',
              background: '#fff',
              border: '1px solid var(--hairline)',
              padding: '8px 13px',
              borderRadius: 'var(--radius-button)',
              cursor: 'pointer',
              textDecoration: 'none',
            }}
          >
            {link.label} →
          </button>
        ))}
        <button
          type="button"
          onClick={() => console.navigate('step_detail', item.id)}
          style={{
            fontSize: 12.5,
            fontWeight: 600,
            color: '#0B6F68',
            background: '#fff',
            border: '1px solid #C9E5E1',
            padding: '8px 13px',
            borderRadius: 'var(--radius-button)',
            cursor: 'pointer',
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLElement).style.background = '#F1EFEA';
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLElement).style.background = '#fff';
          }}
        >
          Open full trace →
        </button>
      </div>

      {/* EXECUTION TRACE card */}
      {item.trace ? (
        <ExecutionTraceCard trace={item.trace} spans={item.spans ?? []} />
      ) : null}

      {/* JURY card with per-dimension verdict summary */}
      <JuryCard jury={item.jury} judges={item.judges ?? []} isSeeded={item.isSeeded ?? false} />

      {/* AGENT REASONING (teal-tinted) — only when a reasoning trace was captured.
          Staged drafts have none yet; we never render an empty/fabricated trace. */}
      {item.thinking.length > 0 ? (
        <div
          style={{
            border: '1px solid var(--reasoning-border)',
            borderRadius: 'var(--radius-card)',
            background: 'var(--reasoning-bg)',
            padding: 'var(--pad-card)',
            display: 'grid',
            gap: 12,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="label" style={{ color: 'var(--reasoning-text)' }}>Agent reasoning</span>
            <span className="mono" style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--auto-chip-text)' }}>
              jury {item.jury.confidence.toFixed(2)}
            </span>
          </div>
          <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 10 }}>
            {item.thinking.map((step, i) => (
              <li key={i} style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
                <span
                  className="mono"
                  style={{
                    flex: '0 0 auto',
                    width: 18,
                    height: 18,
                    borderRadius: '50%',
                    display: 'grid',
                    placeItems: 'center',
                    fontSize: 10,
                    color: '#fff',
                    background: 'var(--teal)',
                    marginTop: 1,
                  }}
                >
                  {i + 1}
                </span>
                <span style={{ fontSize: 13, lineHeight: 1.55, color: 'var(--reasoning-text)' }}>{step}</span>
              </li>
            ))}
          </ol>
        </div>
      ) : null}

      {item.context ? (
        <Section label="Replying to">
          <div style={{ fontSize: 13.5, color: 'var(--text-secondary)', fontStyle: 'italic' }}>“{item.context}”</div>
        </Section>
      ) : null}

      {item.subject ? (
        <Section label="Subject">
          <div style={{ fontSize: 14, fontWeight: 600 }}>{item.subject}</div>
        </Section>
      ) : null}

      <Section
        label={
          staged
            ? 'Draft (staged — not sent)'
            : isFailed
              ? 'Draft (not sent)'
              : item.type === 'POST'
                ? 'Published'
                : 'Sent'
        }
      >
        <div style={{ fontSize: 14, lineHeight: 1.6, color: 'var(--ink)', whiteSpace: 'pre-wrap' }}>{item.content}</div>
      </Section>

      {/* expander */}
      {hasThread || hasComments ? (
        <div style={{ display: 'grid', gap: 12 }}>
          <button
            type="button"
            onClick={onToggleThread}
            style={{
              justifySelf: 'start',
              font: 'inherit',
              fontSize: 13,
              fontWeight: 600,
              color: 'var(--accent-dark)',
              background: 'transparent',
              border: '1px solid var(--reasoning-border)',
              borderRadius: 'var(--radius-button)',
              padding: '7px 12px',
              cursor: 'pointer',
            }}
          >
            {threadOpen ? 'Hide' : expandLabel}
          </button>

          {threadOpen && hasThread ? <Thread item={item} /> : null}
          {threadOpen && hasComments ? <Comments item={item} /> : null}
        </div>
      ) : null}
    </div>
  );
}

function Thread({ item }: { item: ActivityItem }) {
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      {item.thread!.map((m, i) => {
        const out = m.role === 'out';
        return (
          <div key={i} style={{ display: 'flex', justifyContent: out ? 'flex-end' : 'flex-start' }}>
            <div style={{ maxWidth: '78%', display: 'grid', gap: 3 }}>
              {m.name ? (
                <span className="label" style={{ fontSize: 9, textAlign: out ? 'right' : 'left' }}>{m.name}</span>
              ) : null}
              <div
                style={{
                  fontSize: 13,
                  lineHeight: 1.5,
                  padding: '9px 12px',
                  borderRadius: 'var(--radius-card)',
                  background: out ? 'var(--auto-chip-bg)' : 'var(--surface-alt)',
                  color: out ? 'var(--reasoning-text)' : 'var(--text-secondary-2)',
                  border: `1px solid ${out ? 'var(--reasoning-border)' : 'var(--hairline)'}`,
                }}
              >
                {m.text}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Comments({ item }: { item: ActivityItem }) {
  return (
    <div style={{ display: 'grid', gap: 8 }}>
      {item.comments!.map((c, i) => (
        <div
          key={i}
          style={{
            border: '1px solid var(--hairline)',
            borderRadius: 'var(--radius-card)',
            background: 'var(--surface)',
            padding: '10px 12px',
            display: 'grid',
            gap: 4,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 12.5, fontWeight: 600 }}>{c.name}</span>
            {c.autoReplied ? (
              <Chip tone="teal" style={{ marginLeft: 'auto' }}>auto-replied</Chip>
            ) : null}
          </div>
          <div style={{ fontSize: 13, color: 'var(--text-secondary-2)', lineHeight: 1.5 }}>{c.text}</div>
        </div>
      ))}
    </div>
  );
}

function AutonomyChip({ mode, style }: { mode: AutonomyMode; style?: CSSProperties }) {
  const auto = mode === 'AUTO';
  return (
    <Chip tone={auto ? 'teal' : 'amber'} style={style}>
      <Dot color={auto ? 'var(--auto-chip-dot)' : 'var(--amber-dot)'} size={6} />
      {AUTONOMY_LABEL[mode]}
    </Chip>
  );
}

function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: 'grid', gap: 6 }}>
      <span className="label">{label}</span>
      {children}
    </div>
  );
}

function countByFilter(items: ActivityItem[]): Record<QueueFilter, number> {
  return {
    ALL: items.length,
    OUTREACH: items.filter((a) => a.type === 'OUTREACH').length,
    REPLIES: items.filter((a) => a.type === 'COMMENT' || a.type === 'DM').length,
    POSTS: items.filter((a) => a.type === 'POST').length,
  };
}
