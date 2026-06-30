'use client';

/**
 * StepDetailScreen (Module 3, T10+T12) — step-level trace drill-down page.
 * Routed via navigate('step_detail', actionId); drill-only, NOT in NAV_ITEMS.
 *
 * Resolves the action by console.contextId via adapter.getActivity (mirrors
 * ActivityScreen:46-64). After the item resolves, console.setContext(null) is called.
 *
 * HONESTY RULES (spec §5) — strictly enforced:
 *  1. model/tokens/latency "—" → <NotCapturedBadge>; the placeholder is never printed.
 *  2. latency "—" → badge; RunEvent.ms is real, stays plain (not applicable here).
 *  3. tool/MCP calls: no field exists on any GraphQL type → static badge, reason cited.
 *  4. RAG/KB chunks: no field exists → static badge, reason cited.
 *  5. CoT = judges[].reasoning ONLY when judge_rationale is populated; a score-string
 *     pattern (e.g. "voice x · safety y · appr z") is rendered as-is — never authored
 *     into prose. The JuryCard (not this file) renders judge reasoning.
 *  6. self_consistency null → <NotCapturedBadge label="self-consistency — not captured (pre-Phase-5)">.
 *  7. Confidence weights not exposed → badge in JuryMath step 7 ("per-dimension weights not exposed").
 *  8. Langfuse: button ONLY when run.traceUrl != null; URL is NEVER constructed client-side.
 *  9. RunEvent ids null until B3 → keep buttons hidden (not applicable on this page).
 * 10. Empty run dropdown → "no sub-step trace" (RunsScreen only; not applicable here).
 */

import {
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';
import { useData } from '@/lib/data/DataProvider';
import { useAsync } from '@/lib/useAsync';
import { useConsole } from '@/state/console-store';
import { Skeleton, ErrorState, EmptyState } from './states';
import { Chip, channelLabel, clockTime, typeLabel, type ChipTone } from './console-bits';
import { Dot } from './icons';
import { AUTONOMY_LABEL, CHANNEL_COLOR, WORKER_COLOR } from '@/lib/tokens';
import type {
  Action,
  ActivityItem,
  AutonomyMode,
  Escalation,
  Gate,
  JuryDecision,
  Run,
} from '@/lib/data/models';
import { ExecutionTraceCard } from './trace/ExecutionTraceCard';
import { JuryCard } from './trace/JuryCard';
import { NotCapturedBadge } from './trace/NotCapturedBadge';

// ── Outcome chip tone map ────────────────────────────────────────────────────
const OUTCOME_TONE: Record<ActivityItem['outcome']['kind'], ChipTone> = {
  success: 'success',
  teal: 'teal',
  neutral: 'neutral',
};

/** Map a pending Review-queue draft into the ActivityItem shape so "Open
 *  reasoning" resolves it. Carries the REAL Action core (incl. jury, gates,
 *  judges, run linkage) verbatim and adds ONLY truthful staged metadata — no
 *  fabricated trace, engagement, spans, or outcome. */
function stagedStepFromAction(a: Action): ActivityItem {
  return {
    ...a,
    autonomy: 'APPROVE_FIRST',
    content: a.draft,
    outcome: { label: 'Staged · pending approval', kind: 'neutral' },
    thinking: [],
    engagement: [],
    thread: [],
    comments: [],
    runId: a.runId ?? null,
    trace: null,
    judges: a.judges ?? [],
    spans: [],
    links: [],
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Main screen component
// ─────────────────────────────────────────────────────────────────────────────

export function StepDetailScreen() {
  const { adapter, tenantId } = useData();
  const consoleStore = useConsole();

  // Load the full activity list — mirrors the same call ActivityScreen uses.
  const activity = useAsync<ActivityItem[]>(
    () => adapter.getActivity(tenantId),
    [tenantId],
  );
  // ALSO load the pending Review-queue drafts (live source). "Open reasoning"
  // can target a draft that has not executed yet — without this it resolves to
  // nothing and the page dead-ends on "No step selected" (an orphan). We map each
  // draft into the ActivityItem shape with NO fabricated trace/engagement so the
  // jury reasoning it DOES have renders, and the honest "not captured" badges
  // cover the rest. Mirrors ActivityScreen's staged routing.
  const isLive = adapter.source === 'live';
  const staged = useAsync<ActivityItem[]>(
    () =>
      isLive
        ? adapter.getReviewQueue(tenantId).then((as) => as.map(stagedStepFromAction))
        : Promise.resolve([]),
    [tenantId, isLive],
  );
  // Load runs in parallel — needed only for Langfuse URL gating (spec §2.6).
  const runs = useAsync<Run[]>(
    () => adapter.getRuns(tenantId),
    [tenantId],
  );

  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Executed work + staged drafts share one lookup so a deep-link to either kind
  // resolves to the EXACT item — never the wrong one, never an orphan.
  const allItems = useMemo(
    () => [...(activity.data ?? []), ...(staged.data ?? [])],
    [activity.data, staged.data],
  );
  const itemsReady = activity.data !== undefined && staged.data !== undefined;

  // Resolve contextId → selectedId once the data loads, then clear contextId so
  // it does not bleed into a subsequent nav.
  useEffect(() => {
    if (consoleStore.contextId && itemsReady) {
      const found = allItems.find((a) => a.id === consoleStore.contextId);
      if (found) {
        setSelectedId(consoleStore.contextId);
        consoleStore.setContext(null);
      }
    }
  }, [consoleStore.contextId, allItems, itemsReady, consoleStore]);

  const item = useMemo(
    () =>
      selectedId
        ? (allItems.find((a) => a.id === selectedId) ?? null)
        : null,
    [allItems, selectedId],
  );

  // Resolve the owning run for the Langfuse link gate (spec §2.6).
  const run = useMemo(
    () =>
      runs.data && item?.runId
        ? (runs.data.find((r) => r.id === item.runId) ?? null)
        : null,
    [runs.data, item?.runId],
  );

  if (!itemsReady && (activity.loading || staged.loading)) {
    return <Skeleton rows={6} label="Loading step detail…" />;
  }
  const loadError = activity.error ?? staged.error;
  if (loadError) {
    return (
      <ErrorState
        error={loadError}
        onRetry={() => {
          activity.reload();
          staged.reload();
        }}
      />
    );
  }
  if (!item) {
    return (
      <EmptyState
        title="No step selected"
        hint="Navigate to a step from the Activity screen to view its full trace."
      />
    );
  }

  return <StepDetail item={item} run={run} />;
}

// ─────────────────────────────────────────────────────────────────────────────
// Detail body — renders when an item is resolved
// ─────────────────────────────────────────────────────────────────────────────

function StepDetail({ item, run }: { item: ActivityItem; run: Run | null }) {
  // Capture traceUrl once so the closure is safe and TSC narrows to string.
  const traceUrl = run?.traceUrl ?? null;

  return (
    <div
      style={{
        padding: 'var(--pad-section)',
        maxWidth: 900,
        marginInline: 'auto',
        display: 'grid',
        gap: 22,
      }}
    >
      {/* ── Section 1: Header ───────────────────────────────────────────────── */}
      {/* type/channel/autonomy/outcome — REAL. role = item.worker — REAL. */}
      <div style={{ display: 'grid', gap: 8 }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            flexWrap: 'wrap',
          }}
        >
          <span style={{ fontSize: 18, fontWeight: 700, color: 'var(--ink)' }}>
            {typeLabel(item.type)}
          </span>
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              fontSize: 13,
              color: 'var(--text-secondary)',
            }}
          >
            <Dot color={CHANNEL_COLOR[item.channel]} size={8} />
            {channelLabel(item.channel)}
          </span>
          <AutonomyChip mode={item.autonomy} />
          <Chip tone={OUTCOME_TONE[item.outcome.kind]}>{item.outcome.label}</Chip>
          <span
            className="mono"
            style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-muted)' }}
          >
            {clockTime(item.createdAt)}
          </span>
        </div>

        {/* worker = item.worker (REAL role); target for context */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            flexWrap: 'wrap',
          }}
        >
          <span
            className="mono"
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: WORKER_COLOR[item.worker],
            }}
          >
            {item.worker}
          </span>
          <span style={{ fontSize: 13, color: 'var(--text-secondary-2)' }}>
            {item.target}
          </span>
        </div>
      </div>

      {/* ── Section 2: ExecutionTraceCard ───────────────────────────────────── */}
      {/* trace.id/spans REAL; latency/model/tokens → <NotCapturedBadge> (spec §5 rules 1-2). */}
      <ExecutionTraceCard trace={item.trace ?? null} spans={item.spans ?? []} />

      {/* ── Section 3: JuryMath — 11-step derivation (spec §2.5) ────────────── */}
      <JuryMath jury={item.jury} escalation={item.escalation} gates={item.gates} />

      {/* ── Section 4: JuryCard — per-judge rationale + inspector modal ──────── */}
      {/* reasoning is REAL when judge_rationale populated; score-string renders as-is. */}
      <JuryCard
        jury={item.jury}
        judges={item.judges ?? []}
        isSeeded={item.isSeeded ?? false}
      />

      {/* ── Section 5: Gates — REAL ─────────────────────────────────────────── */}
      {item.gates && item.gates.length > 0 ? (
        <Section label="Gates">
          <div style={{ display: 'grid', gap: 8 }}>
            {item.gates.map((g) => (
              <div
                key={g.label}
                style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}
              >
                <span
                  style={{
                    color: g.ok ? '#157F4B' : '#B42318',
                    fontWeight: 700,
                    width: 14,
                    flex: '0 0 auto',
                  }}
                >
                  {g.ok ? '✓' : '✗'}
                </span>
                <span style={{ color: 'var(--text-secondary)' }}>{g.label}</span>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      {/* ── Section 6: Tool / MCP calls — STATIC BADGE ──────────────────────── */}
      {/* No field on any GraphQL type; individual tool/MCP records not captured. */}
      <Section label="Tool / MCP calls">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <NotCapturedBadge label="tool/MCP calls" />
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            Node-level spans only — individual tool/MCP call records are not captured.
          </span>
        </div>
      </Section>

      {/* ── Section 7: RAG / KB chunks — STATIC BADGE ───────────────────────── */}
      {/* No field on any type; no KB chunk provenance is recorded per-step. */}
      <Section label="RAG / KB chunks">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <NotCapturedBadge label="RAG/KB chunks" />
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            No KB chunk provenance recorded — retrieval inputs are not captured per-step.
          </span>
        </div>
      </Section>

      {/* ── Section 8: Sent links (item.links) — REAL (sent only) ───────────── */}
      {item.links && item.links.length > 0 ? (
        <Section label="Sent">
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {item.links.map((link, i) => (
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
          </div>
        </Section>
      ) : null}

      {/* ── Section 9: Langfuse trace link — GATED per spec §2.6 ────────────── */}
      {/*
       * URL is resolved from run.traceUrl via adapter.getRuns.
       * Button rendered ONLY when traceUrl != null.
       * Disabled chip shown when Langfuse is not configured.
       * The URL is NEVER constructed client-side.
       */}
      <Section label="Observability">
        {traceUrl !== null ? (
          <button
            type="button"
            onClick={() => window.open(traceUrl, '_blank')}
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: 'var(--accent-dark)',
              background: 'var(--nav-active-bg)',
              border: '1px solid var(--reasoning-border)',
              padding: '8px 14px',
              borderRadius: 'var(--radius-button)',
              cursor: 'pointer',
            }}
          >
            View Langfuse trace →
          </button>
        ) : (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              fontSize: 12,
              fontWeight: 500,
              color: 'var(--text-muted)',
              background: 'var(--surface-alt)',
              border: '1px solid var(--hairline)',
              padding: '6px 12px',
              borderRadius: 'var(--radius-button)',
              cursor: 'default',
              opacity: 0.7,
            }}
          >
            Langfuse not configured (owner step)
          </span>
        )}
      </Section>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// JuryMath — 11-step derivation (spec §2.5)
//
// Step sources:
//  1. Per-judge raw voice/safety/appr    [REAL] via jury.judges (JudgeVote[])
//     or fallback to dimensions[].jurorBreakdown when jury.judges absent.
//  2. Per-judge pooled = (v+s+a)/3       [REAL, arithmetic shown]
//  3. Per-judge pass/fail floor          [REAL] via JudgeVote.overall or jurorBreakdown.vote
//  4. Per-dim mean                       [REAL] via dimensions[].score
//  5. Per-dim verdict vs threshold       [REAL] via dimensions[].verdict + .threshold
//  6. Self-consistency                   [REAL via B1] null → BADGE "not captured (pre-Phase-5)"
//  7. Pooled confidence: inputs REAL,    weights → BADGE "per-dimension weights not exposed"
//  8. Agreement                          [REAL] via jury.agreement
//  9. Threshold compare                  [REAL] confidence vs threshold
// 10. Route taken                        [REAL] via item.escalation
// 11. Gate summary                       [REAL] via item.gates
// ─────────────────────────────────────────────────────────────────────────────

interface JuryMathProps {
  jury: JuryDecision;
  escalation: Escalation;
  gates: Gate[];
}

// Table header / data cell styles (shared across the JuryMath component).
const TH: CSSProperties = {
  fontSize: 10.5,
  fontWeight: 700,
  color: 'var(--text-secondary)',
  padding: '4px 10px',
  textAlign: 'left',
  borderBottom: '1px solid var(--hairline)',
  fontFamily: "'IBM Plex Mono', monospace",
};
const TD: CSSProperties = {
  fontSize: 11.5,
  fontFamily: "'IBM Plex Mono', monospace",
  padding: '4px 10px',
  color: 'var(--text-secondary-2)',
};

function JuryMath({ jury, escalation, gates }: JuryMathProps) {
  // B2 raw per-judge per-dimension votes (may be absent on pre-B2 rows).
  const judgeVotes = jury.judges ?? [];
  const hasJudgeVotes = judgeVotes.length > 0;
  const dims = jury.dimensions;

  // Estimate judge count for the header when jury.judges is absent.
  const judgeCount =
    hasJudgeVotes
      ? judgeVotes.length
      : dims.length > 0
        ? dims[0].jurorBreakdown.length
        : 0;

  return (
    <div
      style={{
        border: '1px solid var(--hairline)',
        borderRadius: 'var(--radius-card)',
        background: 'var(--surface)',
        padding: 'var(--pad-card)',
        display: 'grid',
        gap: 20,
      }}
    >
      {/* Card header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="label" style={{ color: 'var(--text-secondary)' }}>
          Jury derivation
        </span>
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 11, color: 'var(--teal)' }}>
          {dims.length} dims · {judgeCount} judges
        </span>
      </div>

      {/* ─ Step 1: Per-judge raw scores ─ [REAL] ─────────────────────────── */}
      <MathStep n={1} label="Per-judge raw scores (voice / safety / appr)">
        {hasJudgeVotes ? (
          <table style={{ borderCollapse: 'collapse', width: '100%' }}>
            <thead>
              <tr>
                <th style={TH}>Judge</th>
                <th style={TH}>Voice</th>
                <th style={TH}>Safety</th>
                <th style={TH}>Appr</th>
              </tr>
            </thead>
            <tbody>
              {judgeVotes.map((jv) => (
                <tr key={jv.judge}>
                  <td style={TD}>{jv.judge}</td>
                  <td style={TD}>{jv.voice.toFixed(2)}</td>
                  <td style={TD}>{jv.safety.toFixed(2)}</td>
                  <td style={TD}>{jv.appr.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          // Fallback: use jurorBreakdown (no per-dim split; raw overall score only).
          <div style={{ display: 'grid', gap: 10 }}>
            {dims.map((dim) => (
              <div key={dim.label}>
                <div
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    color: 'var(--text-secondary)',
                    marginBottom: 4,
                  }}
                >
                  {dim.label}
                </div>
                <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                  {dim.jurorBreakdown.map((jb) => (
                    <span
                      key={jb.judge}
                      className="mono"
                      style={{ fontSize: 11, color: 'var(--text-secondary-2)' }}
                    >
                      {jb.judge}: {jb.score.toFixed(2)}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </MathStep>

      {/* ─ Step 2: Per-judge pooled = (v+s+a)/3 ─ [REAL, arithmetic shown] ── */}
      <MathStep n={2} label="Per-judge pooled score = (voice + safety + appr) / 3">
        {hasJudgeVotes ? (
          <div style={{ display: 'grid', gap: 5 }}>
            {judgeVotes.map((jv) => {
              const pooled = (jv.voice + jv.safety + jv.appr) / 3;
              return (
                <div
                  key={jv.judge}
                  className="mono"
                  style={{ fontSize: 11.5, color: 'var(--ink)' }}
                >
                  {jv.judge}:{' '}
                  <span style={{ color: 'var(--text-muted)' }}>
                    ({jv.voice.toFixed(2)} + {jv.safety.toFixed(2)} + {jv.appr.toFixed(2)}) / 3
                  </span>{' '}
                  = <strong style={{ color: 'var(--teal)' }}>{pooled.toFixed(3)}</strong>
                </div>
              );
            })}
          </div>
        ) : (
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            Per-judge pooled requires B2 raw votes (jury.judges). Dimension breakdown shown in step 1.
          </span>
        )}
      </MathStep>

      {/* ─ Step 3: Per-judge pass/fail hard-fail floor ─ [REAL] ─────────────── */}
      <MathStep n={3} label="Per-judge verdict (hard-fail floor check)">
        {hasJudgeVotes ? (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {judgeVotes.map((jv) => {
              const pass = jv.overall >= jury.threshold;
              return (
                <span
                  key={jv.judge}
                  style={{
                    fontSize: 11.5,
                    fontWeight: 600,
                    color: pass ? '#157F4B' : '#B42318',
                    background: pass ? '#E6F4EC' : '#FBE9E6',
                    padding: '3px 10px',
                    borderRadius: 4,
                  }}
                >
                  {jv.judge}: {pass ? '✓' : '✗'}{' '}
                  <span
                    className="mono"
                    style={{ fontWeight: 400, fontSize: 10.5 }}
                  >
                    ({jv.overall.toFixed(2)})
                  </span>
                </span>
              );
            })}
          </div>
        ) : (
          // Fallback: jurorBreakdown carries the per-dim vote flag.
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {dims.flatMap((dim) =>
              dim.jurorBreakdown.map((jb) => {
                const pass = jb.vote === 'pass';
                return (
                  <span
                    key={`${jb.judge}-${dim.label}`}
                    style={{
                      fontSize: 11,
                      fontWeight: 600,
                      color: pass ? '#157F4B' : '#B42318',
                      background: pass ? '#E6F4EC' : '#FBE9E6',
                      padding: '2px 8px',
                      borderRadius: 4,
                    }}
                  >
                    {jb.judge} ({dim.label}): {pass ? '✓' : '✗'}
                  </span>
                );
              }),
            )}
          </div>
        )}
      </MathStep>

      {/* ─ Step 4: Per-dim mean across judges ─ [REAL] ───────────────────────── */}
      <MathStep n={4} label="Per-dimension mean across judges">
        <div style={{ display: 'grid', gap: 6 }}>
          {dims.map((dim) => (
            <div
              key={dim.label}
              style={{ display: 'flex', alignItems: 'center', gap: 10 }}
            >
              <span
                style={{ fontSize: 13, color: 'var(--text-secondary)', flex: 1 }}
              >
                {dim.label}
              </span>
              <span
                className="mono"
                style={{ fontSize: 12, fontWeight: 700, color: 'var(--teal)' }}
              >
                {dim.score.toFixed(3)}
              </span>
            </div>
          ))}
        </div>
      </MathStep>

      {/* ─ Step 5: Per-dim verdict vs threshold ─ [REAL] ────────────────────── */}
      <MathStep n={5} label="Per-dimension verdict vs threshold">
        <div style={{ display: 'grid', gap: 6 }}>
          {dims.map((dim) => {
            const pass = dim.verdict === 'pass';
            return (
              <div
                key={dim.label}
                style={{ display: 'flex', alignItems: 'center', gap: 10 }}
              >
                <span
                  style={{ fontSize: 13, color: 'var(--text-secondary)', flex: 1 }}
                >
                  {dim.label}
                </span>
                <span
                  className="mono"
                  style={{ fontSize: 11, color: 'var(--text-muted)' }}
                >
                  {dim.score.toFixed(3)} {pass ? '≥' : '<'} {dim.threshold.toFixed(2)}
                </span>
                <span
                  style={{
                    fontSize: 11,
                    fontWeight: 600,
                    color: pass ? '#157F4B' : '#B42318',
                    background: pass ? '#E6F4EC' : '#FBE9E6',
                    padding: '2px 8px',
                    borderRadius: 4,
                    flex: '0 0 auto',
                  }}
                >
                  {pass ? '✓ pass' : '✗ fail'}
                </span>
              </div>
            );
          })}
        </div>
      </MathStep>

      {/* ─ Step 6: Self-consistency ─ [REAL via B1; null → BADGE] ──────────── */}
      <MathStep n={6} label="Self-consistency (generation stability probe)">
        {jury.selfConsistency != null ? (
          <span
            className="mono"
            style={{ fontSize: 13, fontWeight: 700, color: 'var(--teal)' }}
          >
            {jury.selfConsistency.toFixed(2)}
          </span>
        ) : (
          <NotCapturedBadge label="self-consistency — not captured (pre-Phase-5)" />
        )}
      </MathStep>

      {/* ─ Step 7: Pooled confidence — inputs REAL, weights BADGE ────────────── */}
      <MathStep n={7} label="Pooled confidence — weighted combination of dimension means">
        <div style={{ display: 'grid', gap: 6 }}>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            Inputs (dim means):{' '}
            {dims.map((d) => `${d.label} ${d.score.toFixed(3)}`).join(' · ')}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              Weights:
            </span>
            <NotCapturedBadge label="per-dimension weights not exposed" />
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            Output (pooled confidence):{' '}
            <span
              className="mono"
              style={{ fontWeight: 700, color: 'var(--teal)' }}
            >
              {jury.confidence.toFixed(3)}
            </span>
          </div>
        </div>
      </MathStep>

      {/* ─ Step 8: Agreement ─ [REAL] ────────────────────────────────────────── */}
      <MathStep n={8} label="Judge agreement">
        <span
          className="mono"
          style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}
        >
          {jury.agreement}
        </span>
      </MathStep>

      {/* ─ Step 9: Threshold comparison ─ [REAL] ─────────────────────────────── */}
      <MathStep n={9} label="Threshold comparison">
        <div className="mono" style={{ fontSize: 12.5 }}>
          confidence{' '}
          <strong style={{ color: 'var(--teal)' }}>
            {jury.confidence.toFixed(3)}
          </strong>{' '}
          {jury.confidence >= jury.threshold ? '≥' : '<'} threshold{' '}
          {jury.threshold.toFixed(2)}{' '}
          <span
            style={{
              marginLeft: 8,
              fontSize: 11,
              fontWeight: 700,
              color: jury.confidence >= jury.threshold ? '#157F4B' : '#B42318',
              background:
                jury.confidence >= jury.threshold ? '#E6F4EC' : '#FBE9E6',
              padding: '2px 8px',
              borderRadius: 4,
            }}
          >
            {jury.confidence >= jury.threshold ? '✓ cleared' : '✗ below threshold'}
          </span>
        </div>
      </MathStep>

      {/* ─ Step 10: Route taken ─ [REAL] ─────────────────────────────────────── */}
      <MathStep n={10} label="Route taken">
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <span
            className="mono"
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: 'var(--text-muted)',
              background: 'var(--surface-alt)',
              border: '1px solid var(--hairline)',
              padding: '2px 8px',
              borderRadius: 4,
            }}
          >
            {escalation.kind}
          </span>
          <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
            {escalation.label}
          </span>
        </div>
      </MathStep>

      {/* ─ Step 11: Gate summary ─ [REAL] ────────────────────────────────────── */}
      <MathStep n={11} label="Gate summary">
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {gates.map((g) => (
            <span
              key={g.label}
              style={{
                fontSize: 11.5,
                fontWeight: 600,
                color: g.ok ? '#157F4B' : '#B42318',
                background: g.ok ? '#E6F4EC' : '#FBE9E6',
                padding: '3px 10px',
                borderRadius: 4,
              }}
            >
              {g.ok ? '✓' : '✗'} {g.label}
            </span>
          ))}
        </div>
      </MathStep>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Shared helpers
// ─────────────────────────────────────────────────────────────────────────────

/** Numbered step row for the JuryMath derivation display. */
function MathStep({
  n,
  label,
  children,
}: {
  n: number;
  label: string;
  children: ReactNode;
}) {
  return (
    <div style={{ display: 'grid', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span
          className="mono"
          style={{
            width: 20,
            height: 20,
            borderRadius: '50%',
            background: 'var(--teal)',
            color: '#fff',
            fontSize: 10,
            fontWeight: 700,
            display: 'grid',
            placeItems: 'center',
            flex: '0 0 auto',
          }}
        >
          {n}
        </span>
        <span
          style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-secondary)' }}
        >
          {label}
        </span>
      </div>
      <div style={{ paddingLeft: 28 }}>{children}</div>
    </div>
  );
}

/** Teal/amber autonomy chip — mirrors ActivityScreen.AutonomyChip. */
function AutonomyChip({
  mode,
  style: cssStyle,
}: {
  mode: AutonomyMode;
  style?: CSSProperties;
}) {
  const auto = mode === 'AUTO';
  return (
    <Chip tone={auto ? 'teal' : 'amber'} style={cssStyle}>
      <Dot
        color={auto ? 'var(--auto-chip-dot)' : 'var(--amber-dot)'}
        size={6}
      />
      {AUTONOMY_LABEL[mode]}
    </Chip>
  );
}

/** Labelled section wrapper (label above, content below). */
function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: 'grid', gap: 6 }}>
      <span className="label">{label}</span>
      {children}
    </div>
  );
}
