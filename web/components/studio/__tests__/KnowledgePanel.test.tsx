import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { KnowledgePanel } from '../KnowledgePanel';

/**
 * KnowledgePanel manages the persistent per-tenant document store. Proves it lists the
 * ACTIVE docs (GET), uploads a new one (POST with name + content), removes one
 * (POST /remove with id), and — with no endpoint — shows the honest not-connected note
 * instead of faking a store.
 */
afterEach(() => {
  vi.restoreAllMocks();
});

function jsonRes(body: unknown) {
  return { ok: true, json: async () => body };
}

describe('KnowledgePanel', () => {
  it('lists active docs and removes one by id', async () => {
    const fetchMock = vi
      .fn()
      // initial GET list
      .mockResolvedValueOnce(
        jsonRes({ ok: true, documents: [{ id: 'doc_1', name: 'Brand Playbook', summary: 'Austin color studio.' }] }),
      )
      // POST /remove
      .mockResolvedValueOnce(jsonRes({ ok: true, id: 'doc_1', removed: true }))
      // refresh GET after remove
      .mockResolvedValueOnce(jsonRes({ ok: true, documents: [] }));
    vi.stubGlobal('fetch', fetchMock);

    render(<KnowledgePanel endpoint="http://api/studio/documents" />);
    await waitFor(() => expect(screen.getByText('Brand Playbook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Remove Brand Playbook/i }));
    await waitFor(() => expect(screen.queryByText('Brand Playbook')).not.toBeInTheDocument());

    const removeCall = fetchMock.mock.calls[1];
    expect(removeCall[0]).toBe('http://api/studio/documents/remove');
    expect(JSON.parse(removeCall[1].body).id).toBe('doc_1');
  });

  it('uploads a pasted document with its name', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonRes({ ok: true, documents: [] })) // initial list
      .mockResolvedValueOnce(jsonRes({ ok: true, id: 'doc_2', name: 'Playbook' })) // upload
      .mockResolvedValueOnce(jsonRes({ ok: true, documents: [{ id: 'doc_2', name: 'Playbook' }] })); // refresh
    vi.stubGlobal('fetch', fetchMock);

    render(<KnowledgePanel endpoint="http://api/studio/documents" />);
    await waitFor(() => expect(screen.getByText(/No documents yet/i)).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText('Document name'), { target: { value: 'Playbook' } });
    fireEvent.change(screen.getByLabelText('Document text'), {
      target: { value: 'We are a woman-owned Austin color studio.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Add document/i }));

    await waitFor(() => expect(screen.getByText('Playbook')).toBeInTheDocument());
    const postCall = fetchMock.mock.calls[1];
    expect(postCall[0]).toBe('http://api/studio/documents');
    const body = JSON.parse(postCall[1].body);
    expect(body.name).toBe('Playbook');
    expect(body.content).toContain('Austin color studio');
  });

  it('shows the honest not-connected note in preview (no endpoint)', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<KnowledgePanel />);
    expect(screen.getByText(/needs the live studio backend/i)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
