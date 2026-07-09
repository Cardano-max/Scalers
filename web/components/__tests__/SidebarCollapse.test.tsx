import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AppShell } from '../AppShell';
import { DataProvider } from '@/lib/data/DataProvider';
import { ConsoleProvider } from '@/state/console-store';
import { MockAdapter } from '@/lib/data/mock-adapter';

/**
 * Ctrl/Cmd+B collapses the left sidebar to an icon rail — VS Code / Cursor parity.
 * The shortcut is a real window keydown handler; a visible chevron does the same.
 */
function renderShell() {
  return render(
    <DataProvider adapter={new MockAdapter()} tenantId="northwind">
      <ConsoleProvider>
        <AppShell />
      </ConsoleProvider>
    </DataProvider>,
  );
}

describe('AppShell — Ctrl+B collapsible sidebar', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('starts expanded, then Ctrl+B collapses to the icon rail', async () => {
    const { container } = renderShell();
    const aside = container.querySelector('aside')!;
    expect(aside.getAttribute('data-collapsed')).toBe('false');
    // expanded shows the brand subtitle + Workspace section label
    expect(await screen.findByText('Operator Console')).toBeInTheDocument();

    fireEvent.keyDown(window, { key: 'b', ctrlKey: true });

    expect(aside.getAttribute('data-collapsed')).toBe('true');
    expect(screen.queryByText('Operator Console')).not.toBeInTheDocument();
    // collapsed persists the choice for next load
    expect(window.localStorage.getItem('scalers.sidebar.collapsed')).toBe('1');
  });

  it('the visible chevron toggle expands it again', async () => {
    const { container } = renderShell();
    const aside = container.querySelector('aside')!;
    fireEvent.keyDown(window, { key: 'b', metaKey: true });
    expect(aside.getAttribute('data-collapsed')).toBe('true');

    fireEvent.click(screen.getByRole('button', { name: /Expand sidebar/i }));
    expect(aside.getAttribute('data-collapsed')).toBe('false');
  });
});
