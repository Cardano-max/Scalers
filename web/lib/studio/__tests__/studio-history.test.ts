/**
 * CustomerAcq-tlv.3 regression — the client transcript must never render
 * internals, even over OLD persisted rows (the 672-turn live history).
 *
 * The live drive proved studioChatHistory carries: role='thinking' rows (raw
 * chain-of-thought), raw "[planner] {json…" rows, 24x duplicate per-lead
 * analyst rows, raw CellExecutionError text, and double-persisted operator
 * turns. `sanitizeBackendTurns` is the defensive FE gate: it must clean all of
 * that WITHOUT touching a healthy conversation.
 */
import { describe, expect, it } from 'vitest';
import { sanitizeBackendTurns, type BackendChatTurn } from '../studio-history';

let seq = 0;
function row(role: string, text: string): BackendChatTurn {
  seq += 1;
  return {
    id: `t${seq}`,
    sessionId: 's',
    seq,
    role,
    text,
    model: null,
    createdAt: `2026-07-03T00:00:${String(seq % 60).padStart(2, '0')}Z`,
  };
}

describe('sanitizeBackendTurns', () => {
  it('drops role=thinking rows entirely', () => {
    const rows = [
      row('operator', 'run it'),
      row('thinking', 'The operator is asking me to... Let me be honest'),
      row('host', 'On it.'),
    ];
    const out = sanitizeBackendTurns(rows);
    expect(out.map((r) => r.role)).toEqual(['operator', 'host']);
  });

  it('dedupes adjacent identical turns (double-persisted operator rows)', () => {
    const rows = [
      row('operator', 'run a win-back campaign'),
      row('operator', 'run a win-back campaign'),
      row('host', 'Launching now.'),
    ];
    const out = sanitizeBackendTurns(rows);
    expect(out).toHaveLength(2);
    expect(out[0].role).toBe('operator');
  });

  it('keeps legitimate repeated messages that are NOT adjacent', () => {
    const rows = [row('operator', 'yes'), row('host', 'confirm?'), row('operator', 'yes')];
    expect(sanitizeBackendTurns(rows)).toHaveLength(3);
  });

  it('collapses a run of per-lead analyst spam into one turn carrying the real count', () => {
    const rows = [
      row('operator', 'go'),
      ...Array.from({ length: 24 }, () =>
        row('analyst', '[analyst] open-warm-lead · objection=none-found · open warm lead'),
      ),
      row('host', 'done'),
    ];
    const out = sanitizeBackendTurns(rows);
    const analyst = out.filter((r) => r.role === 'analyst');
    expect(analyst).toHaveLength(1);
    expect(analyst[0].text).toContain('24');
    expect(analyst[0].text).not.toContain('[analyst]');
    expect(analyst[0].text).not.toContain('·');
  });

  it('rewrites legacy raw planner JSON into a human line', () => {
    const rows = [
      row('planner', '[planner] {"targets": {"category": "all", "scope": "whole studio"'),
    ];
    const out = sanitizeBackendTurns(rows);
    expect(out).toHaveLength(1);
    expect(out[0].text).not.toContain('{');
    expect(out[0].text).not.toContain('[planner]');
  });

  it('rewrites raw cell-error text into an honest one-liner', () => {
    const rows = [
      row(
        'critic',
        "[critic] verdict=error (0.0) · critic cell failed: CellExecutionError: cell 'critic' failed",
      ),
    ];
    const out = sanitizeBackendTurns(rows);
    expect(out).toHaveLength(1);
    expect(out[0].text).not.toContain('CellExecutionError');
    // honest: still says a check failed, does not pretend success
    expect(out[0].text.toLowerCase()).toContain('fail');
  });

  it('strips the legacy [role] prefix from single pipeline turns', () => {
    const rows = [row('strategist', '[strategist] warm win-back angle | book consults')];
    const out = sanitizeBackendTurns(rows);
    expect(out[0].text).not.toContain('[strategist]');
    expect(out[0].text).toContain('warm win-back angle');
  });

  it('rewrites the legacy "[plan] revised" host row into a human line', () => {
    const rows = [
      row(
        'host',
        "[plan] revised: goal='Win back lapsed clients' audience='Lapsed clients' channels=['Email', 'Instagram']",
      ),
    ];
    const out = sanitizeBackendTurns(rows);
    expect(out[0].text).not.toContain('[plan]');
    expect(out[0].text).not.toContain("'");
    expect(out[0].text).toContain('Win back lapsed clients');
    expect(out[0].text).toContain('Email, Instagram');
  });

  it('strips the legacy "[generate]" tag from host rows', () => {
    const rows = [row('host', '[generate] Angel example-grounded campaign: 2 draft(s) staged')];
    const out = sanitizeBackendTurns(rows);
    expect(out[0].text).not.toContain('[generate]');
    expect(out[0].text).toContain('Angel example-grounded campaign');
  });

  it('leaves a clean conversation untouched', () => {
    const rows = [
      row('operator', 'brief: fill May'),
      row('host', 'Great — who are we targeting?'),
      row('operator', 'lapsed clients'),
    ];
    expect(sanitizeBackendTurns(rows)).toEqual(rows);
  });
});
