'use client';

/**
 * Minimal, dependency-free markdown renderer for the per-campaign spec doc.
 * Handles exactly the subset the spec assembler emits: # / ## / ### headings,
 * `-` bullets, **bold**, and `code` spans. It does NOT invent or reflow content —
 * unknown lines render verbatim, so an honest-null marker like "_(not recorded)_"
 * shows through unchanged. No new dependency is pulled in.
 */
import { Fragment, type ReactNode } from 'react';

/** Render inline **bold** and `code` within a single line of text. */
function renderInline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  // Split on **bold** and `code`, keeping the delimiters via capture groups.
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  parts.forEach((part, i) => {
    if (!part) return;
    if (part.startsWith('**') && part.endsWith('**')) {
      out.push(
        <strong key={i} style={{ fontWeight: 600 }}>
          {part.slice(2, -2)}
        </strong>,
      );
    } else if (part.startsWith('`') && part.endsWith('`')) {
      out.push(
        <code
          key={i}
          style={{
            fontFamily: "'IBM Plex Mono', monospace",
            fontSize: 11.5,
            background: '#EEEDE7',
            padding: '1px 5px',
            borderRadius: 4,
          }}
        >
          {part.slice(1, -1)}
        </code>,
      );
    } else {
      out.push(<Fragment key={i}>{part}</Fragment>);
    }
  });
  return out;
}

export function SpecMarkdown({ markdown }: { markdown: string }) {
  const lines = markdown.split('\n');
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {lines.map((line, idx) => {
        if (line.startsWith('### ')) {
          return (
            <div key={idx} style={{ fontSize: 12.5, fontWeight: 700, color: '#0B6F68', marginTop: 8 }}>
              {renderInline(line.slice(4))}
            </div>
          );
        }
        if (line.startsWith('## ')) {
          return (
            <div key={idx} style={{ fontSize: 13.5, fontWeight: 700, color: '#1A1A17', marginTop: 12 }}>
              {renderInline(line.slice(3))}
            </div>
          );
        }
        if (line.startsWith('# ')) {
          return (
            <div key={idx} style={{ fontSize: 15.5, fontWeight: 700, color: '#1A1A17', marginBottom: 4 }}>
              {renderInline(line.slice(2))}
            </div>
          );
        }
        if (line.startsWith('- ')) {
          return (
            <div key={idx} style={{ display: 'flex', gap: 7, fontSize: 12.5, lineHeight: 1.5, color: '#3A362F' }}>
              <span style={{ color: '#A8A299', flex: '0 0 auto' }}>•</span>
              <span style={{ minWidth: 0 }}>{renderInline(line.slice(2))}</span>
            </div>
          );
        }
        if (line.trim() === '') {
          return <div key={idx} style={{ height: 2 }} />;
        }
        return (
          <div key={idx} style={{ fontSize: 12.5, lineHeight: 1.5, color: '#3A362F' }}>
            {renderInline(line)}
          </div>
        );
      })}
    </div>
  );
}
