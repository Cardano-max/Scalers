import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { renderMarkdown } from '../markdown';

/** QA 5d: the transcript must render basic markdown, not raw asterisks. */
describe('renderMarkdown — tiny chat renderer', () => {
  it('renders **bold** and *italic* without leaking asterisks', () => {
    const { container } = render(<div>{renderMarkdown('This is **bold** and *soft*.')}</div>);
    expect(container.querySelector('strong')?.textContent).toBe('bold');
    expect(container.querySelector('em')?.textContent).toBe('soft');
    expect(container.textContent).not.toContain('*');
  });

  it('renders bullet and numbered lists as real lists', () => {
    const { container } = render(
      <div>{renderMarkdown('Plan:\n- research\n- draft\n1. verify\n2. hold')}</div>,
    );
    const ul = container.querySelector('ul');
    const ol = container.querySelector('ol');
    expect(ul?.querySelectorAll('li')).toHaveLength(2);
    expect(ol?.querySelectorAll('li')).toHaveLength(2);
    expect(ul?.textContent).toContain('research');
  });

  it('keeps line breaks within a paragraph', () => {
    const { container } = render(<div>{renderMarkdown('line one\nline two')}</div>);
    expect(container.querySelectorAll('br')).toHaveLength(1);
    expect(container.textContent).toContain('line one');
    expect(container.textContent).toContain('line two');
  });

  it('renders `code` spans and headings', () => {
    const { container } = render(<div>{renderMarkdown('# Brief\nuse `stage_publish`')}</div>);
    expect(container.querySelector('code')?.textContent).toBe('stage_publish');
    expect(container.textContent).toContain('Brief');
    expect(container.textContent).not.toContain('#');
  });

  it('falls through unknown syntax as plain text (nothing hidden)', () => {
    const { container } = render(<div>{renderMarkdown('a ~weird~ [thing](x)')}</div>);
    expect(container.textContent).toBe('a ~weird~ [thing](x)');
  });
});
