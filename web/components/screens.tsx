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
import { CampaignStudio } from './studio/CampaignStudio';
import { StepDetailScreen } from './StepDetailScreen';

export const SCREENS: Record<ScreenId, ComponentType> = {
  overview: SmokeScreen,
  review: ReviewScreen,
  activity: ActivityScreen,
  feed: FeedScreen,
  runs: RunsScreen,
  // P2: the Command tab is now the interactive Campaign Studio (scaffold;
  // PreviewStudioAdapter — not yet wired to live agents). Replaces the static
  // CommandScreen brief form per docs/adr/command-campaign-studio.md Decision 7.
  command: CampaignStudio,
  // drill-only — not in NAV_ITEMS; reached via navigate('step_detail', actionId)
  step_detail: StepDetailScreen,
};
