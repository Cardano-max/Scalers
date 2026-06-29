import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ActivityScreen } from '../ActivityScreen';
import { DataProvider } from '@/lib/data/DataProvider';
import { MockAdapter } from '@/lib/data/mock-adapter';

function renderActivity() {
  return render(
    <DataProvider adapter={new MockAdapter()} tenantId="northwind">
      <ActivityScreen />
    </DataProvider>,
  );
}

describe('ActivityScreen — executed work + reasoning, on the mock adapter spine', () => {
  it('renders the seeded completed actions with filter counts', async () => {
    renderActivity();
    // 3 seeded executed actions: 1 outreach, 1 reply, 1 post
    expect(await screen.findByRole('button', { name: /All\s*3/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Outreach\s*1/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Posts\s*1/ })).toBeInTheDocument();
  });

  it('shows both autonomy chips (teal Auto + amber You approved)', async () => {
    renderActivity();
    // the AUTO comment reply and the approved outreach/post are both seeded
    expect((await screen.findAllByText('Auto')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('You approved').length).toBeGreaterThan(0);
  });

  it('detail shows the agent reasoning trace, engagement tiles, and outcome', async () => {
    renderActivity();
    // default selection = first item (the approved outreach)
    expect(await screen.findByText('Agent reasoning')).toBeInTheDocument();
    // numbered reasoning step content
    expect(screen.getByText(/Ingest silver\.contacts/)).toBeInTheDocument();
    // engagement tiles
    expect(screen.getByText('Opened')).toBeInTheDocument();
    expect(screen.getByText('3×')).toBeInTheDocument();
    // the sent content
    expect(screen.getByText(/patient comfort is everything/)).toBeInTheDocument();
  });

  it('"View conversation" expands the reply thread', async () => {
    renderActivity();
    const expander = await screen.findByRole('button', { name: 'View conversation' });
    fireEvent.click(expander);
    // prospect reply bubble appears once expanded
    expect(await screen.findByText('Dr. Priya Anand')).toBeInTheDocument();
    expect(screen.getByText(/Thursday afternoon/)).toBeInTheDocument();
  });

  it('a post shows "View N comments" with auto-replied tags', async () => {
    renderActivity();
    // switch to Posts → the FB post is selected
    fireEvent.click(await screen.findByRole('button', { name: /Posts\s*1/ }));
    const expander = await screen.findByRole('button', { name: 'View 3 comments' });
    fireEvent.click(expander);
    expect(await screen.findByText('Dana R.')).toBeInTheDocument();
    // engagement reagent answered some comments
    expect(screen.getAllByText('auto-replied').length).toBeGreaterThan(0);
  });
});
