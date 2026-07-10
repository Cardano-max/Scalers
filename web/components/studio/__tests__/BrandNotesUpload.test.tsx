import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { BrandNotesUpload } from '../BrandNotesUpload';

/**
 * Brand / strategy notes upload. Proves the control POSTs the picked text file to
 * the real /studio/notes endpoint WITH the session id (so the backend attaches it
 * to the right plan), renders the honest ack, and — with no endpoint — does NOT
 * fake an attach.
 */
function txtFile(name: string, content: string): File {
  return new File([content], name, { type: 'text/plain' });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('BrandNotesUpload', () => {
  it('posts the notes (with sessionId) to the endpoint and shows the ack', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true, filename: 'brand.txt', chars: 42 }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<BrandNotesUpload endpoint="http://api/studio/notes" sessionId="sess-1" />);
    fireEvent.change(screen.getByTestId('brand-notes-input'), {
      target: { files: [txtFile('brand.txt', 'Warm, plain-spoken. No emoji. Fine-line focus.')] },
    });

    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument());
    expect(screen.getByRole('status').textContent).toMatch(/Saved to campaign context/i);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://api/studio/notes');
    const body = JSON.parse(init.body);
    expect(body.sessionId).toBe('sess-1');
    expect(body.filename).toBe('brand.txt');
    expect(body.content).toContain('No emoji');
  });

  it('does not fake an attach without a live endpoint (preview)', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    render(<BrandNotesUpload sessionId="sess-1" />);
    fireEvent.change(screen.getByTestId('brand-notes-input'), {
      target: { files: [txtFile('n.txt', 'hello')] },
    });

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('alert').textContent).toMatch(/the live studio backend/i);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('surfaces a backend error honestly', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ ok: false, error: 'empty notes — no text content' }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<BrandNotesUpload endpoint="http://api/studio/notes" sessionId="s" />);
    fireEvent.change(screen.getByTestId('brand-notes-input'), {
      target: { files: [txtFile('empty.txt', '   ')] },
    });

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('alert').textContent).toContain('empty notes');
  });
});
