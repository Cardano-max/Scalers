/**
 * Inline SVG iconography (no external image assets — handoff). Stroke icons
 * inherit `currentColor`; dots take an explicit color so channel/worker/status
 * dots can be driven from the typed token maps.
 */
import type { ScreenId } from '@/state/console-store';

const base = {
  width: 18,
  height: 18,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
};

export function NavIcon({ id }: { id: ScreenId }) {
  switch (id) {
    case 'voice':
      return (
        <svg {...base} aria-hidden>
          <rect x="9" y="2" width="6" height="11" rx="3" />
          <path d="M5 10a7 7 0 0 0 14 0" />
          <line x1="12" y1="17" x2="12" y2="21" />
          <line x1="8" y1="21" x2="16" y2="21" />
        </svg>
      );
    case 'agency':
      return (
        <svg {...base} aria-hidden>
          <circle cx="12" cy="5" r="2.4" />
          <circle cx="5" cy="18" r="2.4" />
          <circle cx="19" cy="18" r="2.4" />
          <path d="M12 7.4v3.2M12 12.6 6.4 16M12 12.6 17.6 16" />
        </svg>
      );
    case 'overview':
      return (
        <svg {...base} aria-hidden>
          <rect x="3" y="3" width="7" height="7" rx="1.5" />
          <rect x="14" y="3" width="7" height="7" rx="1.5" />
          <rect x="3" y="14" width="7" height="7" rx="1.5" />
          <rect x="14" y="14" width="7" height="7" rx="1.5" />
        </svg>
      );
    case 'review':
      return (
        <svg {...base} aria-hidden>
          <path d="M21 11.5a8.38 8.38 0 0 1-9 8.3 8.5 8.5 0 0 1-3.8-.9L3 20l1.1-3.2A8.38 8.38 0 0 1 12 3.5a8.5 8.5 0 0 1 9 8z" />
        </svg>
      );
    case 'activity':
      return (
        <svg {...base} aria-hidden>
          <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
        </svg>
      );
    case 'feed':
      return (
        <svg {...base} aria-hidden>
          <path d="M4 11a9 9 0 0 1 9 9M4 4a16 16 0 0 1 16 16" />
          <circle cx="5" cy="19" r="1.5" fill="currentColor" stroke="none" />
        </svg>
      );
    case 'runs':
      return (
        <svg {...base} aria-hidden>
          <line x1="8" y1="6" x2="21" y2="6" />
          <line x1="8" y1="12" x2="21" y2="12" />
          <line x1="8" y1="18" x2="21" y2="18" />
          <circle cx="3.5" cy="6" r="1.2" fill="currentColor" stroke="none" />
          <circle cx="3.5" cy="12" r="1.2" fill="currentColor" stroke="none" />
          <circle cx="3.5" cy="18" r="1.2" fill="currentColor" stroke="none" />
        </svg>
      );
    case 'command':
      return (
        <svg {...base} aria-hidden>
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
      );
    default:
      return null;
  }
}

/** A colored status/channel/worker dot. */
export function Dot({
  color,
  size = 8,
  live = false,
}: {
  color: string;
  size?: number;
  live?: boolean;
}) {
  return (
    <span
      aria-hidden
      style={{
        display: 'inline-block',
        width: size,
        height: size,
        borderRadius: '50%',
        background: color,
        boxShadow: live ? `0 0 0 3px ${color}22` : undefined,
        flex: '0 0 auto',
      }}
    />
  );
}
