/**
 * trace-select — the deep-link selection resolver that fixes the core
 * traceability bug.
 *
 * THE BUG IT FIXES: every master/detail screen had TWO competing effects — one
 * that selected the deep-link target (console.contextId) and one that defaulted
 * the selection to the first row. On the render where the list first populated,
 * both effects ran in the same commit; the default-to-first effect read the
 * still-null `selectedId` from its closure and called `setSelectedId(items[0])`
 * AFTER the contextId effect, so the deep-link target was clobbered and the
 * screen always landed on the FIRST item ("Open in Activity" → wrong row).
 *
 * The fix collapses both into ONE pure decision so a deep-link target always
 * wins. Selection priority:
 *   1. the deep-link target (contextId) when it matches a row  ← the crux
 *   2. the current selection when it is still in the list
 *   3. the first row (honest default)
 *   4. null when the list is empty
 *
 * This is pure + framework-free so the id-mapping invariant ("contextId X
 * resolves to row X, never index 0") is unit-tested without a browser.
 */

export interface Identified {
  id: string;
}

/**
 * Resolve which row id should be selected. A matching `contextId` (a deep-link
 * target from `console.navigate(screen, id)`) ALWAYS wins over the first-row
 * default — that is the whole point of the fix.
 */
export function resolveSelectedId<T extends Identified>(
  items: readonly T[],
  contextId: string | null | undefined,
  currentId: string | null | undefined,
): string | null {
  if (items.length === 0) return null;
  if (contextId && items.some((i) => i.id === contextId)) return contextId;
  if (currentId && items.some((i) => i.id === currentId)) return currentId;
  return items[0].id;
}

/**
 * Whether a pending `contextId` deep-link target is present AND resolvable in
 * the current list. The screen uses this to (a) clear the consumed contextId so
 * it does not bleed into a later navigation, and (b) trigger the arrival
 * highlight only for a real deep-link landing (not for the first-row default).
 */
export function isDeepLinkHit<T extends Identified>(
  items: readonly T[],
  contextId: string | null | undefined,
): contextId is string {
  return !!contextId && items.some((i) => i.id === contextId);
}
