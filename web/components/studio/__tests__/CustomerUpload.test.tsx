import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { CustomerUpload } from '../CustomerUpload';

/**
 * PART 4 — customers CSV upload control. Proves the button POSTs the picked file
 * to the real /studio/upload endpoint and renders the HONEST parse ack (rows +
 * columns + "not ingested"), and that with no endpoint it does NOT fake a parse.
 */

function csvFile(name: string, content: string): File {
  return new File([content], name, { type: 'text/csv' });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('CustomerUpload', () => {
  it('posts the CSV to the endpoint and shows the parse ack', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        ok: true,
        filename: 'customers.csv',
        rows: 3,
        columns: ['name', 'email', 'city'],
        sample: [],
        ingested: false,
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<CustomerUpload endpoint="http://api/studio/upload" />);
    const input = screen.getByTestId('customer-csv-input') as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [csvFile('customers.csv', 'name,email,city\nAda,a@x.io,London\n')] },
    });

    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument());
    expect(screen.getByRole('status').textContent).toContain('Uploaded 3 rows');
    expect(screen.getByRole('status').textContent).toContain('name, email, city');
    expect(screen.getByRole('status').textContent).toMatch(/not ingested/i);

    // it really POSTed the file body to the endpoint.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://api/studio/upload');
    expect(JSON.parse(init.body).filename).toBe('customers.csv');
    expect(JSON.parse(init.body).content).toContain('name,email,city');
  });

  it('does not fake a parse when there is no live endpoint (preview)', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    render(<CustomerUpload />);
    const input = screen.getByTestId('customer-csv-input') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [csvFile('c.csv', 'a,b\n1,2\n')] } });

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('alert').textContent).toMatch(/needs the live studio backend/i);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('surfaces a backend error honestly', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ ok: false, error: 'empty file — no CSV content' }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<CustomerUpload endpoint="http://api/studio/upload" />);
    fireEvent.change(screen.getByTestId('customer-csv-input'), {
      target: { files: [csvFile('empty.csv', '   ')] },
    });

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('alert').textContent).toContain('empty file');
  });
});
