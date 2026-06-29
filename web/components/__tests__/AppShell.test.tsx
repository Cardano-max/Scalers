import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { AppShell } from '../AppShell';
import { DataProvider } from '@/lib/data/DataProvider';
import { ConsoleProvider } from '@/state/console-store';
import { MockAdapter } from '@/lib/data/mock-adapter';

function renderShell() {
  return render(
    <DataProvider adapter={new MockAdapter()} tenantId="northwind">
      <ConsoleProvider>
        <AppShell />
      </ConsoleProvider>
    </DataProvider>,
  );
}

describe('AppShell — locked shell', () => {
  it('renders the full nav including Activity, with the amber Review-queue badge', async () => {
    renderShell();
    const nav = await screen.findByRole('navigation');
    for (const label of ['Overview', 'Review queue', 'Activity', 'Live feed', 'Runs', 'Command']) {
      expect(within(nav).getByText(label)).toBeInTheDocument();
    }
    // amber badge = remaining review count (mock seeds 3 escalations)
    expect(await screen.findByLabelText('3 in review queue')).toHaveTextContent('3');
  });

  it('renders the top bar client pill + LangGraph live pill', async () => {
    renderShell();
    expect(await screen.findByText('Northwind Heating & Air')).toBeInTheDocument();
    expect(screen.getByText('HVAC PACK')).toBeInTheDocument();
    expect(screen.getByText('LangGraph · live')).toBeInTheDocument();
  });

  it('renders the harness status card with the master control', async () => {
    renderShell();
    expect(await screen.findByText('Harness running')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Pause harness' })).toBeInTheDocument();
    expect(screen.getByText('Jordan Tran')).toBeInTheDocument();
  });

  it('nav switches the single active screen', async () => {
    renderShell();
    // Overview shows the KPI strip (SmokeScreen)
    expect(await screen.findByText(/Autonomy · today/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Runs/ }));
    expect(await screen.findByText(/WORKFLOW RUNS/)).toBeInTheDocument();
    // Overview KPIs are no longer mounted (single active screen)
    expect(screen.queryByText(/Autonomy · today/)).not.toBeInTheDocument();
  });

  it('Pause/Resume toggles harness state (master control, not autonomy)', async () => {
    renderShell();
    const btn = await screen.findByRole('button', { name: 'Pause harness' });
    fireEvent.click(btn);
    expect(await screen.findByRole('button', { name: 'Resume harness' })).toBeInTheDocument();
    expect(screen.getByText('Harness paused')).toBeInTheDocument();
  });
});
