import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { CampaignStudio } from '../studio/CampaignStudio';
import { StudioChatPanel } from '../studio/StudioChatPanel';
import { PlanDocPanel } from '../studio/PlanDocPanel';
import type { PlanDoc } from '@/lib/data/studio-adapter';

/**
 * P2 scaffolding render test — proves the chat panel + plan-doc panel MOUNT and
 * render their HONEST empty/preview states on the default (not-wired) preview
 * adapter. No fabricated agent conversation is asserted; the test verifies the
 * opposite: that no agent reply appears when the operator sends a message.
 */

describe('CampaignStudio — preview scaffold (not wired to live agents)', () => {
  it('mounts the chat + live-progress + plan panels with their headings', async () => {
    render(<CampaignStudio />);
    expect(screen.getByRole('heading', { name: 'Conversation' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Live progress' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Plan / spec' })).toBeInTheDocument();
    // plan doc loads async
    expect(await screen.findByLabelText('Editable plan document')).toBeInTheDocument();
  });

  it('renders the chat empty state and the honest "not wired" notes', async () => {
    render(<CampaignStudio />);
    // chat empty state
    expect(screen.getByText('No messages yet')).toBeInTheDocument();
    // top-level honesty banner (Preview · not wired pill + banner copy)
    expect(screen.getByText('Preview · not wired')).toBeInTheDocument();
    expect(screen.getByText(/P2\s*scaffolding/i)).toBeInTheDocument();
    // chat panel preview note
    expect(screen.getByText(/no agent will reply/i)).toBeInTheDocument();
    // live-progress empty preview note
    expect(screen.getByText(/No agent steps yet/i)).toBeInTheDocument();
    // plan panel preview note
    expect(await screen.findByText(/Approve and Execute are disabled/i)).toBeInTheDocument();
  });

  it('shows the plan version label and disabled Approve / Execute placeholders', async () => {
    render(<CampaignStudio />);
    const versionLabel = await screen.findByLabelText('Plan version');
    expect(versionLabel.textContent).toMatch(/v0/);
    expect(versionLabel.textContent).toMatch(/Draft/);
    expect(screen.getByRole('button', { name: 'Approve plan' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Execute' })).toBeDisabled();
  });

  it('echoes the operator message locally and fabricates NO agent reply', async () => {
    render(<CampaignStudio />);
    const input = screen.getByLabelText('Message the campaign team');
    fireEvent.change(input, { target: { value: 'Launch summer promo' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    // the operator's own message renders, labeled "You"
    expect(await screen.findByText('Launch summer promo')).toBeInTheDocument();
    expect(screen.getByText('You')).toBeInTheDocument();
    // honesty: no fabricated agent turn appears
    expect(screen.queryByText('Researcher')).not.toBeInTheDocument();
    expect(screen.queryByText('Strategist')).not.toBeInTheDocument();
    expect(screen.queryByText('Copywriter')).not.toBeInTheDocument();
  });
});

describe('StudioChatPanel — empty/preview unit', () => {
  it('renders empty state + preview note and wires Send to onSend', () => {
    const onSend = vi.fn();
    render(<StudioChatPanel turns={[]} onSend={onSend} streamStatus="preview" />);
    expect(screen.getByText('No messages yet')).toBeInTheDocument();
    expect(screen.getByText(/not connected to live agents/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Message the campaign team'), {
      target: { value: 'hello team' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(onSend).toHaveBeenCalledWith('hello team');
  });
});

describe('PlanDocPanel — preview unit', () => {
  const doc: PlanDoc = {
    id: 'plan_1',
    sessionId: 's1',
    version: 0,
    title: 'Campaign plan (preview)',
    body: 'scaffold body',
    status: 'draft',
    updatedAt: '2026-06-30T00:00:00Z',
  };

  it('shows version label, disabled actions, and edits the body locally', () => {
    const onChangeBody = vi.fn();
    render(
      <PlanDocPanel
        doc={doc}
        body="scaffold body"
        onChangeBody={onChangeBody}
        notWired
      />,
    );
    const versionLabel = screen.getByLabelText('Plan version');
    expect(versionLabel.textContent).toMatch(/v0/);
    expect(screen.getByRole('button', { name: 'Approve plan' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Execute' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Editable plan document'), {
      target: { value: 'edited locally' },
    });
    expect(onChangeBody).toHaveBeenCalledWith('edited locally');
  });
});
