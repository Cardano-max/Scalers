import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// vitest runs with cwd = the web package root.
const css = readFileSync(resolve(process.cwd(), 'app/globals.css'), 'utf8');

describe('design tokens (verbatim from handoff)', () => {
  it('defines the two load-bearing accents exactly', () => {
    expect(css).toContain('--teal: #0f8a82'); // automation / healthy
    expect(css).toContain('--amber-text: #9a6b00'); // human-in-the-loop / escalated
    expect(css).toContain('--amber-dot: #d99405');
  });

  it('defines the channel dot colors', () => {
    expect(css).toContain('--channel-instagram: #7a5af8');
    expect(css).toContain('--channel-facebook: #2563c9');
  });

  it('entrance motion is TRANSFORM-ONLY (never gate content behind opacity:0)', () => {
    // Extract the slide-in keyframe body and assert it contains no opacity.
    const match = css.match(/@keyframes slide-in\s*\{([\s\S]*?)\}\s*\}/);
    expect(match).not.toBeNull();
    const body = match![1];
    expect(body).toContain('transform');
    expect(body.toLowerCase()).not.toContain('opacity');
  });
});
