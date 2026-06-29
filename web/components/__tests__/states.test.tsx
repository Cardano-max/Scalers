import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AsyncBoundary } from '../states';

describe('AsyncBoundary — never a blank screen', () => {
  const child = (d: string[]) => <div>loaded:{d.join(',')}</div>;

  it('shows a skeleton while loading', () => {
    render(
      <AsyncBoundary loading data={undefined} error={undefined} empty={false}>
        {child}
      </AsyncBoundary>,
    );
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('shows an error with a working retry', () => {
    const onRetry = vi.fn();
    render(
      <AsyncBoundary
        loading={false}
        data={undefined}
        error={new Error('kkg.4 unreachable')}
        empty={false}
        onRetry={onRetry}
      >
        {child}
      </AsyncBoundary>,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('kkg.4 unreachable');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it('shows an empty state when the engine has no data', () => {
    render(
      <AsyncBoundary
        loading={false}
        data={[]}
        error={undefined}
        empty
        emptyTitle="Queue clear"
      >
        {child}
      </AsyncBoundary>,
    );
    expect(screen.getByText('Queue clear')).toBeInTheDocument();
  });

  it('renders children once data is loaded', () => {
    render(
      <AsyncBoundary loading={false} data={['a', 'b']} error={undefined} empty={false}>
        {child}
      </AsyncBoundary>,
    );
    expect(screen.getByText('loaded:a,b')).toBeInTheDocument();
  });
});
