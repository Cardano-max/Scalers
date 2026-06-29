/**
 * Canonical SPAN_KIND_STYLE map — deduplicates the identical copies that live
 * in ActivityScreen.tsx:37-43 and RunsScreen.tsx:12-18.
 *
 * Values per spec §1.1:
 *   tool / llm  → teal text + teal tint bg
 *   jury        → amber text + amber tint bg
 *   gate        → green text + green tint bg
 *   decision    → green text + green tint bg
 */
import type { Span } from '@/lib/data/models';

export const SPAN_KIND_STYLE: Record<Span['kind'], { color: string; bg: string }> = {
  tool:     { color: '#0B6F68', bg: '#E1F1EF' },
  llm:      { color: '#0B6F68', bg: '#E1F1EF' },
  jury:     { color: '#9A6B00', bg: '#FBF0D9' },
  gate:     { color: '#157F4B', bg: '#E6F4EC' },
  decision: { color: '#157F4B', bg: '#E6F4EC' },
};
