'use client';

/**
 * TEST MODE surfaces (ju1.5, operator order #1) — SERVER-DRIVEN, never hardcoded.
 *
 * `useTenantMeta()` reads GET /tenants/{id} through the adapter; the banner and
 * the per-draft badge render ONLY when the server says `testMode: true`. A tenant
 * with no row (ladies8391, the legacy dev fixture) or transport failure renders
 * nothing — and that is safe, because the UI is NOT the defense: the engine's
 * `check_send_allowed` gate (ju1.1) refuses the send server-side regardless.
 */
import { useData } from '@/lib/data/DataProvider';
import { useAsync } from '@/lib/useAsync';
import type { TenantMeta } from '@/lib/data/models';

export function useTenantMeta(): { meta: TenantMeta | null; testMode: boolean } {
  const { adapter, tenantId } = useData();
  const { data } = useAsync(
    // Tolerate partial fake adapters (older tests): no method -> honest null,
    // same as a transport failure — the server gate is the real defense.
    () =>
      typeof adapter.getTenantMeta === 'function'
        ? adapter.getTenantMeta(tenantId)
        : Promise.resolve(null),
    [adapter, tenantId],
  );
  const meta = data ?? null;
  return { meta, testMode: meta?.testMode === true };
}

/** The tenant-level banner, mounted once above the active screen. */
export function TestModeBanner() {
  const { meta, testMode } = useTenantMeta();
  if (!testMode) return null;
  return (
    <div
      role="status"
      aria-label="Test mode"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '8px 24px',
        background: 'var(--warning-bg, #fff7e6)',
        borderBottom: '1px solid var(--warning-border, #f0c36d)',
        color: 'var(--warning-text, #7a5200)',
        fontSize: 13,
        fontWeight: 600,
      }}
    >
      <span
        style={{
          fontSize: 10,
          fontWeight: 800,
          letterSpacing: '0.06em',
          padding: '2px 8px',
          borderRadius: 'var(--radius-pill)',
          background: 'var(--warning-text, #7a5200)',
          color: '#fff',
        }}
      >
        TEST MODE
      </span>
      <span>
        Real customer sends disabled
        {meta?.name ? ` for ${meta.name}` : ''} — approvals stage drafts as HELD;
        the server refuses any live send{typeof meta?.allowlistSize === 'number'
          ? ` (allowlist: ${meta.allowlistSize} operator address${meta.allowlistSize === 1 ? '' : 'es'})`
          : ''}.
      </span>
    </div>
  );
}

/** Compact per-draft badge for queue rows / the detail header. */
export function TestModeChip() {
  return (
    <span
      aria-label="Test mode draft"
      style={{
        fontSize: 9.5,
        fontWeight: 800,
        letterSpacing: '0.05em',
        padding: '2px 7px',
        borderRadius: 'var(--radius-pill)',
        background: 'var(--warning-bg, #fff7e6)',
        border: '1px solid var(--warning-border, #f0c36d)',
        color: 'var(--warning-text, #7a5200)',
        whiteSpace: 'nowrap',
      }}
    >
      TEST MODE
    </span>
  );
}
