/**
 * markdown.tsx — a TINY markdown renderer for chat transcripts (QA 5d).
 *
 * The studio host replies in light markdown; the transcript used to show raw
 * asterisks. This renders just the basics — **bold**, *italic* / _italic_,
 * `code`, bullet / numbered lists, headings, and line breaks — as React nodes.
 * No dependency, no dangerouslySetInnerHTML, no HTML parsing: unknown syntax
 * falls through as plain text, so nothing is ever hidden or invented.
 */
import type { ReactNode } from 'react';
import { Fragment } from 'react';

/** Inline pass: **bold**, *italic*, _italic_, `code`. Single-level, no nesting. */
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const re = /(\*\*([^*\n]+)\*\*|\*([^*\n]+)\*|_([^_\n]+)_|`([^`\n]+)`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const key = `${keyPrefix}_i${i++}`;
    if (m[2] != null) nodes.push(<strong key={key}>{m[2]}</strong>);
    else if (m[3] != null) nodes.push(<em key={key}>{m[3]}</em>);
    else if (m[4] != null) nodes.push(<em key={key}>{m[4]}</em>);
    else if (m[5] != null)
      nodes.push(
        <code
          key={key}
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '0.92em',
            background: 'var(--surface-alt)',
            border: '1px solid var(--hairline)',
            borderRadius: 4,
            padding: '0 4px',
          }}
        >
          {m[5]}
        </code>,
      );
    last = m.index + m[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

const BULLET_RE = /^\s*[-*•]\s+(.*)$/;
const ORDERED_RE = /^\s*(\d+)[.)]\s+(.*)$/;
const HEADING_RE = /^\s*(#{1,4})\s+(.*)$/;

type Block =
  | { kind: 'ul'; items: string[] }
  | { kind: 'ol'; items: string[] }
  | { kind: 'heading'; text: string }
  | { kind: 'para'; lines: string[] };

function toBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  for (const rawLine of text.split('\n')) {
    const line = rawLine.replace(/\s+$/, '');
    const bullet = BULLET_RE.exec(line);
    const ordered = ORDERED_RE.exec(line);
    const heading = HEADING_RE.exec(line);
    const lastBlock = blocks[blocks.length - 1];
    if (bullet) {
      if (lastBlock?.kind === 'ul') lastBlock.items.push(bullet[1]);
      else blocks.push({ kind: 'ul', items: [bullet[1]] });
    } else if (ordered) {
      if (lastBlock?.kind === 'ol') lastBlock.items.push(ordered[2]);
      else blocks.push({ kind: 'ol', items: [ordered[2]] });
    } else if (heading) {
      blocks.push({ kind: 'heading', text: heading[2] });
    } else if (line.trim() === '') {
      // blank line closes the current paragraph/list
      if (lastBlock?.kind === 'para' && lastBlock.lines.length > 0) {
        blocks.push({ kind: 'para', lines: [] });
      }
    } else if (lastBlock?.kind === 'para') {
      lastBlock.lines.push(line);
    } else {
      blocks.push({ kind: 'para', lines: [line] });
    }
  }
  return blocks.filter((b) => b.kind !== 'para' || b.lines.length > 0);
}

/** Render markdown-ish chat text to React nodes. */
export function renderMarkdown(text: string): ReactNode {
  const blocks = toBlocks(text);
  if (blocks.length === 0) return text;
  return (
    <>
      {blocks.map((b, bi) => {
        const key = `md_b${bi}`;
        const gap = bi < blocks.length - 1 ? 6 : 0;
        if (b.kind === 'ul' || b.kind === 'ol') {
          const List = b.kind === 'ul' ? 'ul' : 'ol';
          return (
            <List key={key} style={{ margin: `0 0 ${gap}px`, paddingLeft: 20 }}>
              {b.items.map((item, ii) => (
                <li key={`${key}_li${ii}`} style={{ margin: '1px 0' }}>
                  {renderInline(item, `${key}_li${ii}`)}
                </li>
              ))}
            </List>
          );
        }
        if (b.kind === 'heading') {
          return (
            <p key={key} style={{ margin: `0 0 ${gap}px`, fontWeight: 650 }}>
              {renderInline(b.text, key)}
            </p>
          );
        }
        return (
          <p key={key} style={{ margin: `0 0 ${gap}px` }}>
            {b.lines.map((line, li) => (
              <Fragment key={`${key}_l${li}`}>
                {li > 0 && <br />}
                {renderInline(line, `${key}_l${li}`)}
              </Fragment>
            ))}
          </p>
        );
      })}
    </>
  );
}
