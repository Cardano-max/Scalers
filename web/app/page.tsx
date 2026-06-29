import { AppShell } from '@/components/AppShell';

/**
 * The console is a single-page app shell with in-app screen switching (one
 * `screen` state value), not route-per-screen — matching the locked handoff.
 */
export default function Page() {
  return <AppShell />;
}
