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
import { ReviewScreen } from './ReviewScreen';
import { ActivityScreen } from './ActivityScreen';
import { RunsScreen } from './RunsScreen';
import { FeedScreen } from './FeedScreen';
import { CommandScreen } from './CommandScreen';
import { StepDetailScreen } from './StepDetailScreen';

export const SCREENS: Record<ScreenId, ComponentType> = {
  overview: SmokeScreen,
  review: ReviewScreen,
  activity: ActivityScreen,
  feed: FeedScreen,
  runs: RunsScreen,
  command: CommandScreen,
  // drill-only — not in NAV_ITEMS; reached via navigate('step_detail', actionId)
  step_detail: StepDetailScreen,
};
