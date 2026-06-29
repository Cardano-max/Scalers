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
import { AsyncBoundary } from './states';
import { Dot } from './icons';
import { Chip, Tag, channelLabel, clockTime, matchesFilter, typeLabel, type ChipTone, type QueueFilter } from './console-bits';
import { AUTONOMY_LABEL, CHANNEL_COLOR, WORKER_COLOR } from '@/lib/tokens';
import type { ActivityItem, AutonomyMode } from '@/lib/data/models';

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

export function ActivityScreen() {
  const { adapter, tenantId } = useData();
  const activity = useAsync<ActivityItem[]>(() => adapter.getActivity(tenantId), [tenantId]);

  const [filter, setFilter] = useState<QueueFilter>('ALL');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [threadOpen, setThreadOpen] = useState(false);

  const items = useMemo(() => activity.data ?? [], [activity.data]);
  const filtered = useMemo(() => items.filter((a) => matchesFilter(a.type, filter)), [items, filter]);
  const counts = useMemo(() => countByFilter(items), [items]);

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
            loading={activity.loading}
            error={activity.error}
            data={activity.data}
            empty={filtered.length === 0}
            onRetry={activity.reload}
            emptyTitle="No activity yet"
            emptyHint="Executed and approved actions land here with the agent’s reasoning."
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
          <Chip tone={OUTCOME_TONE[item.outcome.kind]} style={{ marginLeft: 'auto' }}>
            {item.outcome.label}
          </Chip>
        </div>
        <div
          style={{ fontSize: 13, color: 'var(--ink)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
        >
          {item.subject ?? item.content}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 7 }}>
          <span className="mono" style={{ fontSize: 11, color: WORKER_COLOR[item.worker] }}>{item.worker}</span>
          <AutonomyChip mode={item.autonomy} style={{ marginLeft: 'auto' }} />
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
  const hasThread = !!item.thread && item.thread.length > 0;
  const hasComments = !!item.comments && item.comments.length > 0;
  const expandLabel = hasComments ? `View ${item.comments!.length} comments` : 'View conversation';

  return (
    <div style={{ padding: 'var(--pad-section)', maxWidth: 760, display: 'grid', gap: 18 }}>
      {/* header */}
      <div style={{ display: 'grid', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 17, fontWeight: 600 }}>{typeLabel(item.type)}</span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--text-secondary)' }}>
            <Dot color={CHANNEL_COLOR[item.channel]} size={8} />
            {channelLabel(item.channel)}
          </span>
          <AutonomyChip mode={item.autonomy} />
          <span className="mono" style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-muted)' }}>{clockTime(item.createdAt)}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 13.5, color: 'var(--text-secondary-2)' }}>{item.target}</span>
          <Chip tone={OUTCOME_TONE[item.outcome.kind]}>{item.outcome.label}</Chip>
        </div>
      </div>

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

      {/* AGENT REASONING (teal-tinted) */}
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

      <Section label={item.type === 'POST' ? 'Published' : 'Sent'}>
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
