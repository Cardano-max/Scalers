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
import { useConsoleOptional } from '@/state/console-store';
import { useAsync } from '@/lib/useAsync';
import { AsyncBoundary } from './states';
import { Dot } from './icons';
import { Chip, ProviderErrorPanel, Tag, actionIntent, channelLabel, clockTime, matchesFilter, typeLabel, type QueueFilter } from './console-bits';
import { CHANNEL_COLOR, WORKER_COLOR } from '@/lib/tokens';
import type { Action, ActionEvidence, ActionType } from '@/lib/data/models';
import { SendModeToggle } from './studio/send-mode';
// --- traceability spine (additive) ---
import { useTraceArrival } from '@/lib/useTraceArrival';
import { LineageChips } from './trace/LineageChips';
import { ConfidenceEvidence } from './trace/ConfidenceEvidence';
import { EvidenceProvenance } from './trace/EvidenceProvenance';

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
  // Deep Review deep-link: the studio result/review surface navigates here with the
  // staged action id as contextId — focus that row once the queue loads. Optional read
  // so the screen still renders in isolation (unit tests mount it without a provider).
  const consoleCtx = useConsoleOptional();
  const contextId = consoleCtx?.contextId ?? null;
  const queue = useAsync<Action[]>(() => adapter.getReviewQueue(tenantId), [tenantId]);
  // Arrival highlight (traceability spine): pulse + scroll to a deep-linked draft.
  const { highlightId, trigger: triggerArrival, scrollRef } = useTraceArrival();

  const [items, setItems] = useState<Action[] | null>(null);
  const [filter, setFilter] = useState<QueueFilter>('ALL');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draftText, setDraftText] = useState('');
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);
  // Send mode for approve→publish: default Test (safe redirect) vs explicit Live. The
  // operator's per-draft #11 complaint ("I approved a real email and it had [TEST]")
  // lives on THIS path, so the toggle governs every approve in the queue.
  const [liveMode, setLiveMode] = useState(false);
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
    () =>
      list
        .filter((a) => matchesFilter(a.type, filter))
        // NEWEST drafts at the TOP (operator ask). createdAt is ISO-8601, so a
        // lexical compare is chronological; non-mutating (filter already copied).
        .sort((a, b) => (a.createdAt < b.createdAt ? 1 : a.createdAt > b.createdAt ? -1 : 0)),
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

  // Deep-link consumer: when navigated here with a contextId (the Deep-Review button, or
  // a campaign/run/action chip on a feed/run item), select + pulse that EXACT draft.
  // Declared AFTER the default-select effect so this setSelectedId wins over filtered[0]
  // (the "opens first item" race). Self-clears the context so repeated bidirectional
  // navigation re-fires. contextId is read null-safely, so unit tests mount without a
  // provider (the effect is a no-op there).
  useEffect(() => {
    if (contextId && filtered.some((a) => a.id === contextId)) {
      setSelectedId(contextId);
      setEditing(false);
      triggerArrival(contextId);
      consoleCtx?.setContext(null);
    }
  }, [contextId, filtered, triggerArrival, consoleCtx]);

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
      const result = await adapter.approveAction(a.id, a.idempotencyKey, liveMode);
      // HONEST OUTCOME: approve→publish can come back FAILED with the REAL
      // provider error (e.g. an expired Meta token → Graph HTTP 400). Do NOT
      // claim "sent" and do NOT silently drop it — keep the row, flip it to
      // failed in place, and let the detail render the verbatim error so the
      // operator sees WHY. Never a fake success.
      if (result.status === 'FAILED') {
        setItems((prev) =>
          (prev ?? []).map((x) =>
            x.id === a.id ? { ...x, status: 'FAILED', lastError: result.lastError ?? null } : x,
          ),
        );
        showToast(`Send failed — ${truncate(result.lastError ?? 'provider error', 60)}`, 'amber');
        return;
      }
      removeAndAdvance(a.id);
      // Surface the REAL mode the engine routed this send through (Live vs Test), so the
      // operator sees plainly whether a real email went out or it was test-redirected.
      const modeTag = result.mode ? ` · ${result.mode === 'live' ? 'LIVE' : 'TEST'}` : '';
      showToast(`${approveVerb(a.type)} — ${truncate(a.target, 40)}${modeTag}`, 'success');
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
          {/* Approve→publish send mode — default Test (safe), explicit Live is confirm-gated. */}
          <div style={{ marginTop: 10 }}>
            <SendModeToggle live={liveMode} onChange={setLiveMode} disabled={busy} />
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
                    highlighted={a.id === highlightId}
                    scrollRef={a.id === highlightId ? scrollRef : undefined}
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
  highlighted,
  scrollRef,
  onSelect,
}: {
  action: Action;
  selected: boolean;
  highlighted?: boolean;
  scrollRef?: (node: HTMLElement | null) => void;
  onSelect: () => void;
}) {
  const preview = action.subject ?? action.draft;
  return (
    <li>
      <button
        type="button"
        ref={scrollRef}
        className={highlighted ? 'trace-arrive' : undefined}
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
          <span className="mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            {clockTime(action.createdAt)}
          </span>
          <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-muted)' }}>
            Confidence {pct(action.confidence)}%
          </span>
        </div>
        {/* Lineage chips — deep-link to the producing run / agent reasoning / this
            draft. Renders only the ids that genuinely exist (honest-null). */}
        {action.runId || action.campaignId ? (
          <div style={{ marginTop: 7 }}>
            <LineageChips
              lineage={{
                campaignId: action.campaignId,
                runId: action.runId,
                agentRole: action.agentRole,
                actionId: action.id,
              }}
            />
          </div>
        ) : null}
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
  // Evidence/provenance for THIS draft — what it actually used. Fetched through the
  // same adapter the screen reads from, keyed on the selected action id. Real-only:
  // the panel itself omits empty categories and shows an honest line when there is
  // nothing. We render it only after the read resolves so there is no loading flash.
  const { adapter } = useData();
  const [evidence, setEvidence] = useState<ActionEvidence | null>(null);
  const [evidenceLoaded, setEvidenceLoaded] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setEvidence(null);
    setEvidenceLoaded(false);
    adapter
      .getActionEvidence(action.id)
      .then((e) => {
        if (!cancelled) {
          setEvidence(e);
          setEvidenceLoaded(true);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setEvidence(null);
          setEvidenceLoaded(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [adapter, action.id]);

  return (
    <div style={{ padding: 'var(--pad-section)', maxWidth: 1100, marginInline: 'auto', display: 'grid', gap: 18 }}>
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
        {/* Lineage chips — the FULL provenance label set for this draft: campaign / run /
            producing-agent reasoning / this action, the recipient + lead (CSV-row) identity,
            the brand voice used, each cited research source (clickable → opens the URL), the
            confidence reason, plus created / channel / run-level trace. Clickable chips
            deep-link to the EXACT item; everything else is an honest context label, and any
            value the draft genuinely lacks is omitted (never a fake chip). Lead/recipient/
            voice/sources/reason fill in once the evidence read resolves. */}
        <LineageChips
          lineage={{
            campaignId: action.campaignId,
            runId: action.runId,
            agentRole: action.agentRole,
            actionId: action.id,
            createdAt: action.createdAt,
            channel: action.channel,
            traceUrl: action.traceUrl,
            recipient: action.target,
            leadName: evidence?.customer?.name ?? null,
            leadId: evidence?.customer?.customerId ?? null,
            brandVoice:
              evidence?.brandVoice && evidence.brandVoice.used
                ? evidence.brandVoice.source || evidence.brandVoice.tenantId
                : null,
            confidenceReason: evidence?.confidenceReason ?? null,
            sources:
              evidence?.researchSources?.map((s) => ({ url: s.url, title: s.title })) ?? null,
          }}
        />
      </div>

      {/* Plain-language intent + HELD/staged banner: states exactly what approving
          this draft would do, and that NOTHING sends until the operator approves
          (and even then it is held behind the real publish step). */}
      {action.status === 'PENDING' ? (
        <div
          style={{
            border: '1px solid var(--reasoning-border)',
            borderRadius: 'var(--radius-card)',
            background: 'var(--reasoning-bg)',
            padding: '12px 14px',
            display: 'grid',
            gap: 6,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <Dot color={CHANNEL_COLOR[action.channel]} size={8} />
            <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--reasoning-text)' }}>
              {actionIntent(action.type, action.channel, action.target)}
            </span>
            <Chip tone="amber" style={{ marginLeft: 'auto' }}>Staged · awaiting approval</Chip>
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            Held for review — nothing is sent. Approve only stages this for the publish
            step (still gated); Reject discards it.
          </div>
        </div>
      ) : null}

      {/* FAILED approve→publish — show the REAL provider error, not a bare "Failed". */}
      {action.status === 'FAILED' && action.lastError ? (
        <ProviderErrorPanel error={action.lastError} />
      ) : null}

      <AutonomyCard action={action} />

      {/* Human-readable "why this confidence" — built from the real jury/judge
          fields above, with a link to the exact reasoning trace. Not raw JSON. */}
      <ConfidenceEvidence action={action} />

      {/* Evidence / provenance — what this draft actually used (brand voice, CSV
          facts, cited research, tool calls, ...). Real-only + honest-empty. */}
      {evidenceLoaded ? <EvidenceProvenance evidence={evidence} /> : null}

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
      </div>
    </div>
  );
}

function AutonomyCard({ action }: { action: Action }) {
  const { confidence, threshold, jury } = action;
  const [expandedDims, setExpandedDims] = useState<Set<string>>(new Set());
  const [selectedJudge, setSelectedJudge] = useState<string | null>(null);

  const toggleDimension = (label: string) => {
    const next = new Set(expandedDims);
    if (next.has(label)) {
      next.delete(label);
    } else {
      next.add(label);
    }
    setExpandedDims(next);
  };

  const getJudgeDetails = (judgeName: string) => {
    // Find judge in full judges list to get full reasoning
    const judge = action.judges?.find((j) => j.name === judgeName);
    return judge || null;
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
          <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-secondary)' }}>
            {pct(confidence)}%
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
                      <button
                        key={juror.judge}
                        type="button"
                        onClick={() => setSelectedJudge(juror.judge)}
                        style={{
                          all: 'unset',
                          display: 'flex',
                          alignItems: 'center',
                          gap: 8,
                          fontSize: 12,
                          color: 'var(--text-secondary)',
                          cursor: 'pointer',
                          padding: '4px 0',
                          borderRadius: 4,
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
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>Reviewer panel · {jury.agreement}</div>

      {/* deterministic gate chips */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {action.gates.map((g) => (
          <Chip key={g.label} tone={g.ok ? 'success' : 'danger'}>
            <span aria-hidden>{g.ok ? '✓' : '✕'}</span> {g.label}
          </Chip>
        ))}
      </div>

      {/* Judge Inspector Modal */}
      {selectedJudge ? (
        <JudgeInspectorModal
          judge={getJudgeDetails(selectedJudge)}
          isSeeded={action.isSeeded ?? false}
          onClose={() => setSelectedJudge(null)}
        />
      ) : null}
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

function JudgeInspectorModal({
  judge,
  isSeeded,
  onClose,
}: {
  judge: { name: string; score: number; vote: string; reasoning: string } | null;
  isSeeded: boolean;
  onClose: () => void;
}) {
  if (!judge) return null;

  return (
    <>
      {/* overlay */}
      <div
        role="presentation"
        onClick={onClose}
        style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          background: 'rgba(0, 0, 0, 0.4)',
          zIndex: 999,
        }}
      />
      {/* modal */}
      <div
        style={{
          position: 'fixed',
          top: '50%',
          left: '50%',
          transform: 'translate(-50%, -50%)',
          background: 'var(--surface)',
          border: '1px solid var(--hairline)',
          borderRadius: 'var(--radius-card)',
          boxShadow: 'var(--shadow-card)',
          padding: 'var(--pad-card)',
          maxWidth: 500,
          width: 'calc(100% - 32px)',
          maxHeight: '80vh',
          overflow: 'auto',
          zIndex: 1000,
          display: 'grid',
          gap: 16,
        }}
      >
        {/* header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 16, fontWeight: 600, flex: 1 }}>{judge.name}</span>
          {isSeeded && (
            <span
              style={{
                fontSize: 10,
                fontWeight: 700,
                color: '#5D5D5D',
                background: '#F0F0F0',
                padding: '3px 8px',
                borderRadius: 4,
                textTransform: 'uppercase',
                letterSpacing: '0.5px',
              }}
            >
              [DEMO]
            </span>
          )}
          <button
            type="button"
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              fontSize: 18,
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              padding: '0',
              display: 'grid',
              placeItems: 'center',
              width: 24,
              height: 24,
            }}
          >
            ✕
          </button>
        </div>

        {/* verdict badge */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: judge.vote === 'pass' ? '#157F4B' : '#B42318',
              background: judge.vote === 'pass' ? '#E6F4EC' : '#FBE9E6',
              padding: '4px 10px',
              borderRadius: 5,
            }}
          >
            {judge.vote === 'pass' ? '✓ Pass' : '✗ Fail'}
          </span>
          <span className="mono" style={{ fontSize: 12, color: 'var(--text-secondary)', fontWeight: 600 }}>
            {fmt(judge.score)}
          </span>
        </div>

        {/* reasoning section */}
        <div style={{ display: 'grid', gap: 8 }}>
          <span className="label" style={{ fontSize: 10 }}>Reasoning</span>
          <div style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--ink)' }}>
            {judge.reasoning}
          </div>
        </div>

        {/* seeded demo data note */}
        {isSeeded && (
          <div
            style={{
              fontSize: 12,
              color: 'var(--text-secondary)',
              background: '#F9F9F9',
              border: '1px solid var(--hairline-light)',
              borderRadius: 'var(--radius-button)',
              padding: '10px 12px',
              marginTop: 4,
            }}
          >
            ⓘ Seeded demo data — not a live jury run
          </div>
        )}
      </div>
    </>
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
  return Math.round(Math.max(0, Math.min(100, n * 100)));
}
function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
