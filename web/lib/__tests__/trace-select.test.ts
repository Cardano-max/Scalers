import { describe, it, expect } from 'vitest';
import { resolveSelectedId, isDeepLinkHit } from '../trace-select';

/**
 * The CRUX regression test for the traceability bug: a deep-link target
 * (contextId) must resolve to the EXACT matching row — never index 0. This is
 * the pure core of the "Open in Activity / Open run jumps to the first item" fix.
 */
describe('resolveSelectedId — deep-link target wins over the first-row default', () => {
  const items = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];

  it('returns the deep-link target id, NOT index 0', () => {
    // contextId 'c' is the 3rd row; the buggy code returned items[0].id ('a').
    expect(resolveSelectedId(items, 'c', null)).toBe('c');
    expect(resolveSelectedId(items, 'b', null)).toBe('b');
  });

  it('the target wins even when a different row is already selected', () => {
    expect(resolveSelectedId(items, 'c', 'a')).toBe('c');
  });

  it('falls back to the current selection when no deep-link target is pending', () => {
    expect(resolveSelectedId(items, null, 'b')).toBe('b');
  });

  it('falls back to the first row only when there is no target and no valid selection', () => {
    expect(resolveSelectedId(items, null, null)).toBe('a');
    // stale selection no longer in the list → first row
    expect(resolveSelectedId(items, null, 'zzz')).toBe('a');
  });

  it('ignores a target that does not match any row (no false deep-link)', () => {
    // unknown target → not a hit → first-row default, never crash
    expect(resolveSelectedId(items, 'nope', null)).toBe('a');
    expect(resolveSelectedId(items, 'nope', 'b')).toBe('b');
  });

  it('returns null for an empty list', () => {
    expect(resolveSelectedId([], 'c', 'a')).toBeNull();
  });
});

describe('isDeepLinkHit — true only when a real target resolves in the list', () => {
  const items = [{ id: 'a' }, { id: 'b' }];

  it('is true when the target matches a row', () => {
    expect(isDeepLinkHit(items, 'b')).toBe(true);
  });
  it('is false for a missing target, null, or empty list', () => {
    expect(isDeepLinkHit(items, 'zzz')).toBe(false);
    expect(isDeepLinkHit(items, null)).toBe(false);
    expect(isDeepLinkHit([], 'a')).toBe(false);
  });
});
