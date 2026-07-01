'use client';

/**
 * BlueprintBoardPanel — the P1.5 plan-first surface (blueprint #1 + #3).
 *
 * Renders, for the current run, the planner's EXECUTABLE blueprint (the plan built BEFORE
 * drafting) and the durable PROGRESS BOARD (structured run-state), both wired to REAL
 * backend data (GET /studio/run/{id} → RunState.blueprint / RunState.board). This makes the
 * planner step VISIBLE as the first step of the war-room and shows the live board during /
 * after a run.
 *
 * HONESTY: every value is real backend data. A run with no blueprint yet renders an honest
 * "planner has not run" note — never a fabricated plan. The offer_logic shows an objection's
 * REAL substantiated offer, or "no offer" (never an invented code). The planner-model badge
 * says truthfully whether a best-tier model call happened or the deterministic core planned.
 */
import type { CampaignBlueprint, ProgressBoard } from '@/lib/studio/blueprint';
import { boardCompletion, plannerUsedModel, realOfferCount } from '@/lib/studio/blueprint';

interface BlueprintBoardPanelProps {
  blueprint?: CampaignBlueprint | null;
  board?: ProgressBoard | null;
}

const label: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: 0.4,
  color: '#8C877D',
  marginBottom: 4,
  display: 'block',
};

const chip = (bg: string, fg: string): React.CSSProperties => ({
  fontSize: 11,
  fontWeight: 600,
  padding: '2px 8px',
  borderRadius: 6,
  background: bg,
  color: fg,
  fontFamily: "'IBM Plex Mono', monospace",
});

export function BlueprintBoardPanel({ blueprint, board }: BlueprintBoardPanelProps) {
  return (
    <section
      aria-label="Campaign blueprint and progress board"
      style={{
        marginBottom: 16,
        background: '#fff',
        border: '1px solid var(--hairline)',
        borderRadius: 12,
        overflow: 'hidden',
      }}
    >
      <header
        style={{
          padding: '12px 16px',
          borderBottom: '1px solid var(--hairline)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
        }}
      >
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: '#1A1A17' }}>
          Plan &amp; progress
        </h2>
        {blueprint && (
          <span
            aria-label="Planner model"
            title={
              plannerUsedModel(blueprint)
                ? 'The planner ran on the best-tier model (real call).'
                : 'The plan was built by the deterministic core (no model call).'
            }
            style={chip(
              plannerUsedModel(blueprint) ? '#EDE7FB' : '#F5F3F0',
              plannerUsedModel(blueprint) ? '#5B3FC4' : '#6B6461',
            )}
          >
            planner · {blueprint.planner_model}
          </span>
        )}
      </header>

      {!blueprint ? (
        <div
          role="note"
          style={{ margin: 16, fontSize: 13, lineHeight: 1.5, color: '#8C877D' }}
        >
          The planner has not run yet. When a campaign starts, the planner builds the
          executable blueprint first (targets, per-channel quota, offer-logic, stop
          conditions) — it will appear here as the first step.
        </div>
      ) : (
        <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
          {/* Blueprint — the executable plan */}
          <div>
            <span style={label}>Target cohort</span>
            <div style={{ fontSize: 13, color: '#2A2722' }}>{blueprint.targets.description}</div>
          </div>

          {blueprint.angle && (
            <div>
              <span style={label}>Campaign angle</span>
              <div style={{ fontSize: 13, color: '#2A2722' }}>{blueprint.angle}</div>
            </div>
          )}

          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <div>
              <span style={label}>Per-channel quota</span>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {Object.keys(blueprint.per_channel_quota).length === 0 ? (
                  <span style={{ fontSize: 12, color: '#8C877D' }}>no channel chosen</span>
                ) : (
                  Object.entries(blueprint.per_channel_quota).map(([ch, n]) => (
                    <span key={ch} style={chip('#F0F4F8', '#3A5A78')}>
                      {ch}: {n}
                    </span>
                  ))
                )}
              </div>
            </div>
            <div>
              <span style={label}>Assumed objection</span>
              <span style={chip('#FBF3E8', '#8B6F47')}>
                {blueprint.assumed_dominant_objection ?? 'none assumed'}
              </span>
            </div>
          </div>

          <div>
            <span style={label}>
              Offer logic — {realOfferCount(blueprint)} objection(s) with a REAL offer
            </span>
            <ul style={{ margin: 0, paddingLeft: 0, listStyle: 'none', fontSize: 12.5 }}>
              {blueprint.offer_logic.map((r) => (
                <li
                  key={r.objection}
                  style={{
                    display: 'flex',
                    gap: 8,
                    padding: '3px 0',
                    color: r.offer_code ? '#2A2722' : '#A8A299',
                  }}
                >
                  <span style={{ minWidth: 96, fontWeight: 600 }}>{r.objection}</span>
                  <span>
                    {r.offer_code ? (
                      <>
                        <span style={chip('#E8F5EC', '#2E7D46')}>{r.offer_code}</span>
                        {r.substantiated ? ' (substantiated)' : ''}
                      </>
                    ) : (
                      'no offer — draft references no discount'
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          {/* Progress board — the durable structured run-state */}
          <div style={{ borderTop: '1px solid var(--hairline)', paddingTop: 12 }}>
            <span style={label}>
              Progress board{board ? ` · ${board.run_status}` : ''}
            </span>
            {!board ? (
              <div style={{ fontSize: 12, color: '#8C877D' }}>No board snapshot yet.</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{ fontSize: 13, color: '#2A2722' }}>
                  {board.leads_done} / {board.leads_total} leads drafted (
                  {Math.round(boardCompletion(board) * 100)}%)
                </div>
                {board.contradictions.length > 0 && (
                  <div
                    role="alert"
                    style={{
                      fontSize: 12,
                      padding: '6px 10px',
                      background: '#FDEEEE',
                      border: '1px solid #F5C6C6',
                      borderRadius: 8,
                      color: '#9B2C2C',
                    }}
                  >
                    Replan: {board.contradictions.join('; ')}
                  </div>
                )}
                {board.objections_resolved.length > 0 && (
                  <div style={{ fontSize: 12, color: '#46423B' }}>
                    Objections measured: {board.objections_resolved.join(', ')}
                  </div>
                )}
                {board.known.length > 0 && (
                  <div>
                    <span style={label}>Known</span>
                    <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: '#46423B' }}>
                      {board.known.map((k, i) => (
                        <li key={i}>{k}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {board.missing.length > 0 && (
                  <div>
                    <span style={label}>Missing / open</span>
                    <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: '#8C6D3F' }}>
                      {board.missing.map((m, i) => (
                        <li key={i}>{m}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
