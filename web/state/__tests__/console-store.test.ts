import { describe, it, expect } from 'vitest';
import { __reducer, NAV_ITEMS } from '../console-store';

describe('console store — nav + edit reset', () => {
  const start = { screen: 'overview' as const, contextId: null, editing: false, draftText: '' };

  it('leads with the voice-first headline modes then the real-data tabs', () => {
    expect(NAV_ITEMS.map((n) => n.id)).toEqual([
      'voice',
      'agency',
      'command',
      'overview',
      'review',
      'activity',
      'feed',
      'runs',
    ]);
  });

  it('navigating to a new screen RESETS editing state', () => {
    const editing = __reducer(start, { type: 'startEditing', draftText: 'half-written reply' });
    expect(editing.editing).toBe(true);
    expect(editing.draftText).toBe('half-written reply');

    const navigated = __reducer(editing, { type: 'navigate', screen: 'runs' });
    expect(navigated.screen).toBe('runs');
    expect(navigated.editing).toBe(false);
    expect(navigated.draftText).toBe('');
  });

  it('navigating to the SAME screen is a no-op (keeps edit buffer)', () => {
    const editing = __reducer(start, { type: 'startEditing', draftText: 'keep me' });
    const same = __reducer(editing, { type: 'navigate', screen: 'overview' });
    expect(same).toBe(editing); // identity preserved
    expect(same.draftText).toBe('keep me');
  });

  it('SAME-screen nav WITH a deep-link target updates contextId (intra-screen chip jump)', () => {
    // The traceability fix: clicking a chip that targets a specific row on the
    // screen you are already on must re-select that row. A bare same-screen nav
    // stays a no-op (above); one carrying a contextId updates it.
    const onActivity = { ...start, screen: 'activity' as const, contextId: null };
    const jumped = __reducer(onActivity, {
      type: 'navigate',
      screen: 'activity',
      contextId: 'act_77a0b',
    });
    expect(jumped.screen).toBe('activity');
    expect(jumped.contextId).toBe('act_77a0b');
    // editing buffer is preserved (selecting a different row is not a screen switch)
    const editingThenJump = __reducer(
      { ...onActivity, editing: true, draftText: 'half' },
      { type: 'navigate', screen: 'activity', contextId: 'act_x' },
    );
    expect(editingThenJump.draftText).toBe('half');
    expect(editingThenJump.contextId).toBe('act_x');
  });

  it('setDraft updates the buffer; cancelEditing clears it', () => {
    let s = __reducer(start, { type: 'startEditing', draftText: 'a' });
    s = __reducer(s, { type: 'setDraft', draftText: 'ab' });
    expect(s.draftText).toBe('ab');
    s = __reducer(s, { type: 'cancelEditing' });
    expect(s.editing).toBe(false);
    expect(s.draftText).toBe('');
  });
});
