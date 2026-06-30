import { describe, it, expect } from 'vitest';
import { useEffect } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { ActivityScreen } from '../ActivityScreen';
import { RunsScreen } from '../RunsScreen';
import { DataProvider } from '@/lib/data/DataProvider';
import { ConsoleProvider, useConsole, type ScreenId } from '@/state/console-store';
import { MockAdapter } from '@/lib/data/mock-adapter';

/**
 * End-to-end regression for THE bug: a deep-link ("Open in Activity" / "Open
 * run") must land on the EXACT related item, never the first row. We drive the
 * REAL screens on the mock spine, set a deep-link target that is NOT the first
 * row, and assert the detail shows that exact item's content (and not the first
 * item's). Before the fix, the default-select effect clobbered the target and
 * the screen always showed index 0.
 */
function DeepLink({ screen, id }: { screen: ScreenId; id: string }) {
  const console = useConsole();
  useEffect(() => {
    console.navigate(screen, id);
    // run once on mount to simulate arriving via a deep-link nav
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return null;
}

function renderWithDeepLink(node: React.ReactNode, screen: ScreenId, id: string) {
  return render(
    <DataProvider adapter={new MockAdapter()} tenantId="northwind">
      <ConsoleProvider>
        <DeepLink screen={screen} id={id} />
        {node}
      </ConsoleProvider>
    </DataProvider>,
  );
}

describe('deep-link navigation lands on the exact item (not the first)', () => {
  it('Activity: deep-link to the 3rd item selects IT, not item 0', async () => {
    // evt_4b2e8 = the FB "5 AC myths" post (3rd activity row). Item 0 is the
    // Marina Bay Dental outreach ("patient comfort is everything").
    renderWithDeepLink(<ActivityScreen />, 'activity', 'evt_4b2e8');

    // the deep-link target's content is shown in the detail pane
    expect(await screen.findByText(/5 AC myths, busted/)).toBeInTheDocument();
    // and the FIRST item's content is NOT shown (we did not fall back to index 0)
    expect(screen.queryByText(/patient comfort is everything/)).toBeNull();
  });

  it('Runs: deep-link to the 2nd run selects IT, not run 0', async () => {
    // run_4820 = "Outreach batch" (2nd run); run_4821 = "Comment reply" (1st).
    // The note "Rate cap hit on domain warmup" renders ONLY in run_4820's drawer.
    renderWithDeepLink(<RunsScreen />, 'runs', 'run_4820');

    expect(await screen.findByText(/Rate cap hit on domain warmup/)).toBeInTheDocument();
  });

  it('Activity: with NO deep-link target, the first item is the honest default', async () => {
    render(
      <DataProvider adapter={new MockAdapter()} tenantId="northwind">
        <ConsoleProvider>
          <ActivityScreen />
        </ConsoleProvider>
      </DataProvider>,
    );
    // unchanged default behavior: first item (the outreach) is selected
    expect(await screen.findByText(/patient comfort is everything/)).toBeInTheDocument();
  });
});

describe('deep-link target wins even after a default selection settles', () => {
  it('Activity: target is honored once data loads (no flash-to-first stuck state)', async () => {
    renderWithDeepLink(<ActivityScreen />, 'activity', 'evt_77a0b');
    // evt_77a0b = the IG comment reply (filter cadence answer)
    await waitFor(() =>
      expect(screen.getByText(/1-inch filter every 30/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/patient comfort is everything/)).toBeNull();
  });
});
