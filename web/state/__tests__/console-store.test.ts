import { describe, it, expect } from 'vitest';
import { __reducer, NAV_ITEMS } from '../console-store';

describe('console store — nav + edit reset', () => {
  const start = { screen: 'overview' as const, editing: false, draftText: '' };

  it('includes Activity in the locked nav order', () => {
    expect(NAV_ITEMS.map((n) => n.id)).toEqual([
      'overview',
      'review',
      'activity',
      'feed',
      'runs',
      'command',
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

  it('setDraft updates the buffer; cancelEditing clears it', () => {
    let s = __reducer(start, { type: 'startEditing', draftText: 'a' });
    s = __reducer(s, { type: 'setDraft', draftText: 'ab' });
    expect(s.draftText).toBe('ab');
    s = __reducer(s, { type: 'cancelEditing' });
    expect(s.editing).toBe(false);
    expect(s.draftText).toBe('');
  });
});
