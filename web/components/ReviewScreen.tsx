'use client';

/**
 * Review queue (handoff screen 2) — the escalated → human slice. Two-pane
 * master/detail on the typed adapter spine: the LIST reads `getReviewQueue`
 * through the active adapter (mock or live, no code change); the DETAIL renders
 * the Autonomy decision card (confidence vs. threshold, per-dimension jury,
 * deterministic gates) and wires Approve/Reject/Regenerate/Edit to the adapter
 * mutations. Approve/Reject remove the item, advance to the next, and toast.
 *
 * Only the active screen mounts (AppShell), so all local state — selection,
 * filter, inline edit — resets when the operator navigates away (handoff rule).
 */
import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { useData } from '@/lib/data/DataProvider';
import { useAsync } from '@/lib/useAsync';
import { AsyncBoundary } from './states';
import { Dot } from './icons';
import { Chip, Tag, channelLabel, clockTime, matchesFilter, typeLabel, type QueueFilter } from './console-bits';
import { CHANNEL_COLOR, WORKER_COLOR } from '@/lib/tokens';
import type { Action, ActionType } from '@/lib/data/models';

type ToastTone = 'success' | 'neutral' | 'amber';
interface ToastState {
  text: string;
  tone: ToastTone;
}

const FILTERS: Array<{ id: QueueFilter; label: string }> = [
  { id: 'ALL', label: 'All' },
  { id: 'OUTREACH', label: 'Outreach' },
  { id: 'REPLIES', label: 'Replies' },
  { id: 'POSTS', label: 'Posts' },
];

export function ReviewScreen() {
  const { adapter, tenantId } = useData();
  const queue = useAsync<Action[]>(() => adapter.getReviewQueue(tenantId), [tenantId]);

  const [items, setItems] = useState<Action[] | null>(null);
  const [filter, setFilter] = useState<QueueFilter>('ALL');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draftText, setDraftText] = useState('');
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Local working copy of the queue so approve/reject can remove items + advance
  // the selection without a refetch. Seeded once the adapter read resolves.
  useEffect(() => {
    if (queue.data && items === null) setItems(queue.data);
  }, [queue.data, items]);

  const showToast = (text: string, tone: ToastTone) => {
    setToast({ text, tone });
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 2400);
  };
  useEffect(() => () => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
  }, []);

  const list = useMemo(() => items ?? [], [items]);
  const filtered = useMemo(
    () => list.filter((a) => matchesFilter(a.type, filter)),
    [list, filter],
  );
  const counts = useMemo(() => countByFilter(list), [list]);

  // Keep a valid selection within the current filter.
  useEffect(() => {
    if (filtered.length === 0) {
      if (selectedId !== null) setSelectedId(null);
      return;
    }
    if (!selectedId || !filtered.some((a) => a.id === selectedId)) {
      setSelectedId(filtered[0].id);
    }
  }, [filtered, selectedId]);

  const selected = filtered.find((a) => a.id === selectedId) ?? null;

  const selectRow = (id: string) => {
    setSelectedId(id);
    setEditing(false);
  };

  const removeAndAdvance = (id: string) => {
    const idx = filtered.findIndex((a) => a.id === id);
    const remaining = filtered.filter((a) => a.id !== id);
    const next = remaining[idx] ?? remaining[idx - 1] ?? null;
    setItems((prev) => (prev ?? []).filter((a) => a.id !== id));
    setSelectedId(next?.id ?? null);
    setEditing(false);
  };

  const onApprove = async (a: Action) => {
    setBusy(true);
    try {
      await adapter.approveAction(a.id, a.idempotencyKey);
      removeAndAdvance(a.id);
      showToast(`${approveVerb(a.type)} — ${truncate(a.target, 40)}`, 'success');
    } catch (e) {
      showToast(`Approve failed: ${errMsg(e)}`, 'amber');
    } finally {
      setBusy(false);
    }
  };

  const onReject = async (a: Action) => {
    setBusy(true);
    try {
      await adapter.rejectAction(a.id);
      removeAndAdvance(a.id);
      showToast(`Rejected — ${truncate(a.target, 40)}`, 'neutral');
    } catch (e) {
      showToast(`Reject failed: ${errMsg(e)}`, 'amber');
    } finally {
      setBusy(false);
    }
  };

  const onRegenerate = async (a: Action) => {
    setBusy(true);
    try {
      await adapter.regenerateAction(a.id);
      showToast('Regenerating draft…', 'neutral');
    } catch (e) {
      showToast(`Regenerate failed: ${errMsg(e)}`, 'amber');
    } finally {
      setBusy(false);
    }
  };

  const onEdit = (a: Action) => {
    setDraftText(a.draft);
    setEditing(true);
  };
  const onCancelEdit = () => setEditing(false);
  const onSaveEdit = async (a: Action) => {
    setBusy(true);
    try {
      const updated = await adapter.editActionDraft(a.id, draftText);
      setItems((prev) => (prev ?? []).map((x) => (x.id === a.id ? { ...x, draft: updated.draft } : x)));
      setEditing(false);
      showToast('Draft saved', 'neutral');
    } catch (e) {
      showToast(`Save failed: ${errMsg(e)}`, 'amber');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ position: 'relative', display: 'flex', height: '100%', minHeight: 0 }}>
      {/* ---------- LIST (master) ---------- */}
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
            loading={queue.loading}
            error={queue.error}
            data={items ?? queue.data}
            empty={filtered.length === 0}
            onRetry={queue.reload}
            emptyTitle="Queue clear"
            emptyHint="Nothing escalated — the engine is handling everything in policy."
          >
            {() => (
              <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                {filtered.map((a) => (
                  <QueueRow
                    key={a.id}
                    action={a}
                    selected={a.id === selectedId}
                    onSelect={() => selectRow(a.id)}
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
          <DetailPane
            action={selected}
            editing={editing}
            draftText={draftText}
            busy={busy}
            onDraftChange={setDraftText}
            onApprove={() => onApprove(selected)}
            onReject={() => onReject(selected)}
            onRegenerate={() => onRegenerate(selected)}
            onEdit={() => onEdit(selected)}
            onCancelEdit={onCancelEdit}
            onSaveEdit={() => onSaveEdit(selected)}
          />
        ) : (
          <div style={{ padding: 'var(--pad-section)', color: 'var(--text-muted)', textAlign: 'center', marginTop: 40 }}>
            <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-secondary)' }}>Queue clear</div>
            <div style={{ marginTop: 6, fontSize: 13 }}>No action selected — pick a row, or enjoy the empty queue.</div>
          </div>
        )}
      </div>

      {toast ? <Toast toast={toast} /> : null}
    </div>
  );
}

/* ---------------- list row ---------------- */

function QueueRow({
  action,
  selected,
  onSelect,
}: {
  action: Action;
  selected: boolean;
  onSelect: () => void;
}) {
  const preview = action.subject ?? action.draft;
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
        {/* left accent bar on the selected row */}
        <span
          aria-hidden
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            bottom: 0,
            width: 3,
            background: selected ? 'var(--accent)' : 'transparent',
          }}
        />
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          <Tag>{typeLabel(action.type)}</Tag>
          <Dot color={CHANNEL_COLOR[action.channel]} size={7} />
          <Chip tone="amber" style={{ marginLeft: 'auto' }}>
            {action.escalation.label}
          </Chip>
        </div>
        <div
          style={{
            fontSize: 13,
            color: 'var(--ink)',
            fontWeight: 500,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {preview}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 7 }}>
          <span className="mono" style={{ fontSize: 11, color: WORKER_COLOR[action.worker] }}>
            {action.worker}
          </span>
          <span className="mono" style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-muted)' }}>
            conf {fmt(action.confidence)} / {fmt(action.threshold)}
          </span>
        </div>
      </button>
    </li>
  );
}

/* ---------------- detail pane ---------------- */

function DetailPane({
  action,
  editing,
  draftText,
  busy,
  onDraftChange,
  onApprove,
  onReject,
  onRegenerate,
  onEdit,
  onCancelEdit,
  onSaveEdit,
}: {
  action: Action;
  editing: boolean;
  draftText: string;
  busy: boolean;
  onDraftChange: (v: string) => void;
  onApprove: () => void;
  onReject: () => void;
  onRegenerate: () => void;
  onEdit: () => void;
  onCancelEdit: () => void;
  onSaveEdit: () => void;
}) {
  return (
    <div style={{ padding: 'var(--pad-section)', maxWidth: 760, display: 'grid', gap: 18 }}>
      {/* header */}
      <div style={{ display: 'grid', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 17, fontWeight: 600 }}>{typeLabel(action.type)}</span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--text-secondary)' }}>
            <Dot color={CHANNEL_COLOR[action.channel]} size={8} />
            {channelLabel(action.channel)}
          </span>
          <span className="mono" style={{ fontSize: 12, color: 'var(--text-muted)' }}>{action.id}</span>
          <span className="mono" style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-muted)' }}>
            {clockTime(action.createdAt)}
          </span>
        </div>
        <div style={{ fontSize: 13.5, color: 'var(--text-secondary-2)' }}>{action.target}</div>
      </div>

      <AutonomyCard action={action} />

      {action.context ? (
        <Section label="Replying to">
          <div style={{ fontSize: 13.5, color: 'var(--text-secondary)', fontStyle: 'italic' }}>“{action.context}”</div>
        </Section>
      ) : null}

      {action.subject ? (
        <Section label="Subject">
          <div style={{ fontSize: 14, fontWeight: 600 }}>{action.subject}</div>
        </Section>
      ) : null}

      <Section label="Draft">
        {editing ? (
          <textarea
            value={draftText}
            onChange={(e) => onDraftChange(e.target.value)}
            rows={6}
            style={{
              width: '100%',
              font: 'inherit',
              fontSize: 14,
              lineHeight: 1.55,
              color: 'var(--ink)',
              padding: 12,
              borderRadius: 'var(--radius-button)',
              border: '1px solid var(--accent)',
              background: 'var(--surface)',
              resize: 'vertical',
            }}
          />
        ) : (
          <div style={{ fontSize: 14, lineHeight: 1.6, color: 'var(--ink)', whiteSpace: 'pre-wrap' }}>{action.draft}</div>
        )}
      </Section>

      {action.recommendation ? (
        <div style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>
          <span className="label">Recommendation</span> {action.recommendation}
        </div>
      ) : null}

      {/* action row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', paddingTop: 2 }}>
        {editing ? (
          <>
            <Btn kind="primary" onClick={onSaveEdit} disabled={busy}>Save draft</Btn>
            <Btn kind="ghost" onClick={onCancelEdit} disabled={busy}>Cancel</Btn>
          </>
        ) : (
          <>
            <Btn kind="approve" onClick={onApprove} disabled={busy}>{approveLabel(action.type)}</Btn>
            <Btn kind="ghost" onClick={onEdit} disabled={busy}>Edit</Btn>
            <Btn kind="ghost" onClick={onRegenerate} disabled={busy}>Regenerate</Btn>
            <Btn kind="reject" onClick={onReject} disabled={busy}>Reject</Btn>
          </>
        )}
        <span className="mono" style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-faint)' }}>
          {action.idempotencyKey}
        </span>
      </div>
    </div>
  );
}

function AutonomyCard({ action }: { action: Action }) {
  const { confidence, threshold, jury } = action;
  const [expandedDims, setExpandedDims] = useState<Set<string>>(new Set());

  const toggleDimension = (label: string) => {
    const next = new Set(expandedDims);
    if (next.has(label)) {
      next.delete(label);
    } else {
      next.add(label);
    }
    setExpandedDims(next);
  };

  return (
    <div
      style={{
        border: '1px solid var(--hairline)',
        borderRadius: 'var(--radius-card)',
        background: 'var(--surface)',
        boxShadow: 'var(--shadow-card)',
        padding: 'var(--pad-card)',
        display: 'grid',
        gap: 16,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span className="label">Autonomy decision</span>
        <Chip tone="amber" style={{ marginLeft: 'auto' }}>{action.escalation.label}</Chip>
      </div>

      {/* confidence bar with threshold tick */}
      <div style={{ display: 'grid', gap: 6 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <span className="label">Confidence</span>
          <span className="mono" style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-secondary)' }}>
            conf {fmt(confidence)} / {fmt(threshold)}
          </span>
        </div>
        <div style={{ position: 'relative', height: 8, borderRadius: 999, background: 'var(--hairline-light)' }}>
          <div
            style={{
              width: `${pct(confidence)}%`,
              height: '100%',
              borderRadius: 999,
              background: confidence >= threshold ? 'var(--teal)' : 'var(--amber-dot)',
            }}
          />
          {/* threshold tick */}
          <div
            aria-label={`threshold ${fmt(threshold)}`}
            style={{
              position: 'absolute',
              left: `${pct(threshold)}%`,
              top: -3,
              bottom: -3,
              width: 2,
              background: 'var(--ink)',
              transform: 'translateX(-1px)',
            }}
          />
        </div>
      </div>

      {/* per-dimension jury with verdict chips and expandable breakdown */}
      <div style={{ display: 'grid', gap: 12 }}>
        {jury.dimensions.map((d) => {
          const isExpanded = expandedDims.has(d.label);
          const hasBreakdown = d.jurorBreakdown && d.jurorBreakdown.length > 0;
          const verdictPassed = d.verdict === 'pass';
          return (
            <div key={d.label} style={{ display: 'grid', gap: 8 }}>
              <button
                type="button"
                onClick={() => hasBreakdown && toggleDimension(d.label)}
                style={{
                  all: 'unset',
                  cursor: hasBreakdown ? 'pointer' : 'default',
                  display: 'grid',
                  gap: 8,
                }}
              >
                {/* dimension header with verdict chip */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span className="label" style={{ fontSize: 10 }}>{d.label}</span>
                  {hasBreakdown && (
                    <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                      {isExpanded ? '▼' : '▶'}
                    </span>
                  )}
                  <Chip
                    tone={verdictPassed ? 'success' : 'danger'}
                    style={{ marginLeft: 'auto' }}
                  >
                    <span aria-hidden>{verdictPassed ? '✓' : '✕'}</span>
                    {' '}
                    {verdictPassed ? 'Pass' : 'Fail'}
                  </Chip>
                  <span className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                    thr {fmt(d.threshold)}
                  </span>
                </div>

                {/* mini-bar */}
                <div style={{ height: 5, borderRadius: 999, background: 'var(--hairline-light)' }}>
                  <div
                    style={{
                      width: `${pct(d.score)}%`,
                      height: '100%',
                      borderRadius: 999,
                      background: verdictPassed ? 'var(--teal)' : 'var(--amber-dot)',
                    }}
                  />
                </div>
              </button>

              {/* expandable juror breakdown */}
              {isExpanded && hasBreakdown && (
                <div style={{ display: 'grid', gap: 6, paddingLeft: 16, borderLeft: '2px solid var(--hairline-light)' }}>
                  {d.jurorBreakdown.map((juror) => {
                    const jurorPassed = juror.vote === 'pass';
                    return (
                      <div
                        key={juror.judge}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 8,
                          fontSize: 12,
                          color: 'var(--text-secondary)',
                        }}
                      >
                        <span style={{ flex: 1, minWidth: 0 }}>{juror.judge}</span>
                        <span
                          style={{
                            fontSize: 11,
                            fontWeight: 600,
                            color: jurorPassed ? '#157F4B' : '#B42318',
                            background: jurorPassed ? '#E6F4EC' : '#FBE9E6',
                            padding: '2px 6px',
                            borderRadius: 4,
                            flex: '0 0 auto',
                          }}
                        >
                          {jurorPassed ? '✓' : '✕'}
                        </span>
                        <span className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)', flex: '0 0 auto' }}>
                          {fmt(juror.score)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>jury · {jury.agreement}</div>

      {/* deterministic gate chips */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {action.gates.map((g) => (
          <Chip key={g.label} tone={g.ok ? 'success' : 'danger'}>
            <span aria-hidden>{g.ok ? '✓' : '✕'}</span> {g.label}
          </Chip>
        ))}
      </div>
    </div>
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

/* ---------------- buttons + toast ---------------- */

function Btn({
  kind,
  children,
  onClick,
  disabled,
}: {
  kind: 'approve' | 'reject' | 'primary' | 'ghost';
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
}) {
  const styles: Record<string, CSSProperties> = {
    approve: { background: 'var(--success-text)', color: '#fff', border: '1px solid var(--success-text)' },
    primary: { background: 'var(--accent)', color: '#fff', border: '1px solid var(--accent)' },
    reject: { background: 'var(--surface)', color: 'var(--danger-text)', border: '1px solid var(--danger-text)' },
    ghost: { background: 'var(--surface)', color: 'var(--text-secondary)', border: '1px solid var(--hairline-strong)' },
  };
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      style={{
        ...styles[kind],
        font: 'inherit',
        fontSize: 13,
        fontWeight: 600,
        padding: '8px 14px',
        borderRadius: 'var(--radius-button)',
        cursor: disabled ? 'default' : 'pointer',
        opacity: disabled ? 0.55 : 1,
      }}
    >
      {children}
    </button>
  );
}

function Toast({ toast }: { toast: ToastState }) {
  const tone =
    toast.tone === 'success'
      ? { dot: 'var(--success-dot)', text: '#fff' }
      : toast.tone === 'amber'
        ? { dot: 'var(--amber-dot)', text: '#fff' }
        : { dot: 'var(--text-faint)', text: '#fff' };
  return (
    <div
      role="status"
      className="enter"
      style={{
        position: 'absolute',
        left: '50%',
        bottom: 24,
        transform: 'translateX(-50%)',
        display: 'inline-flex',
        alignItems: 'center',
        gap: 10,
        background: 'var(--ink)',
        color: tone.text,
        padding: '10px 16px',
        borderRadius: 'var(--radius-button)',
        boxShadow: 'var(--shadow-toast)',
        fontSize: 13,
        maxWidth: 'min(560px, 80%)',
      }}
    >
      <Dot color={tone.dot} />
      <span>{toast.text}</span>
    </div>
  );
}

/* ---------------- helpers ---------------- */

function countByFilter(items: Action[]): Record<QueueFilter, number> {
  return {
    ALL: items.length,
    OUTREACH: items.filter((a) => a.type === 'OUTREACH').length,
    REPLIES: items.filter((a) => a.type === 'COMMENT' || a.type === 'DM').length,
    POSTS: items.filter((a) => a.type === 'POST').length,
  };
}

function approveLabel(type: ActionType): string {
  return type === 'POST' ? 'Approve & publish' : 'Approve & send';
}
function approveVerb(type: ActionType): string {
  return type === 'POST' ? 'Published' : 'Approved & sent';
}
function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}
function fmt(n: number): string {
  return n.toFixed(2);
}
function pct(n: number): number {
  return Math.max(0, Math.min(100, n * 100));
}
function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
