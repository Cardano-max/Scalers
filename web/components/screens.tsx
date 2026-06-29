'use client';

/**
 * Screen registry — maps each ScreenId to its component. The FE foundation
 * (45v.1) ships the shell + the proven data spine (SmokeScreen on Overview);
 * the real screens replace these entries per their own beads:
 *   Review queue 45v.3 · Activity 45v.4 · Live feed 45v.7 · Runs 45v.5 ·
 *   Command 45v.9 · Overview 45v.8.
 */
import type { ComponentType } from 'react';
import type { ScreenId } from '@/state/console-store';
import { SmokeScreen } from './SmokeScreen';
import { EmptyState } from './states';

function pending(name: string, bead: string): ComponentType {
  const Pending = () => (
    <div style={{ padding: 'var(--pad-section)' }}>
      <EmptyState title={`${name} — coming up`} hint={`Built on this foundation in bead ${bead}.`} />
    </div>
  );
  Pending.displayName = `Pending(${name})`;
  return Pending;
}

export const SCREENS: Record<ScreenId, ComponentType> = {
  overview: SmokeScreen,
  review: pending('Review queue', '45v.3'),
  activity: pending('Activity', '45v.4'),
  feed: pending('Live feed', '45v.7'),
  runs: pending('Runs', '45v.5'),
  command: pending('Command', '45v.9'),
};
