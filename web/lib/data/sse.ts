/**
 * Typed SSE client for the kkg.4 realtime stream.
 *
 * `GET /sse/stream?tenantId=...` multiplexes the 7 canonical events; field
 * names are STABLE (eng1). A feed-only `GET /sse/feed?tenantId=...` exists for
 * the Live-feed screen. This client:
 *   - maps each named SSE event to its typed payload,
 *   - auto-reconnects with capped exponential backoff on drop (the screen is
 *     never lost),
 *   - is testable: the `EventSource` implementation is injectable so unit tests
 *     can drive it without a browser or a live backend.
 */
import type {
  Action,
  FeedEvent,
  Kpis,
  Run,
  RunStep,
  SystemHealth,
  Severity,
} from './models';

/** The canonical SSE event name → payload map (scalers-backend-plan §1.4). */
export interface SSEEventMap {
  'feed.event': FeedEvent;
  'action.created': Action;
  'action.updated': Action;
  'run.updated': Run | RunStep;
  'kpi.updated': Kpis;
  'health.updated': SystemHealth;
  toast: { text: string; severity: Severity };
}

export type SSEEventName = keyof SSEEventMap;

export const SSE_EVENT_NAMES: SSEEventName[] = [
  'feed.event',
  'action.created',
  'action.updated',
  'run.updated',
  'kpi.updated',
  'health.updated',
  'toast',
];

export type SSEHandlers = {
  [K in SSEEventName]?: (data: SSEEventMap[K]) => void;
};

export type SSEStatus = 'connecting' | 'open' | 'reconnecting' | 'closed';

/** Minimal structural type for an EventSource (so we can inject a fake in tests). */
export interface EventSourceLike {
  addEventListener(type: string, listener: (ev: MessageEvent) => void): void;
  close(): void;
  onopen: ((ev: Event) => void) | null;
  onerror: ((ev: Event) => void) | null;
}
export type EventSourceFactory = (url: string) => EventSourceLike;

export interface SSEClientOptions {
  /** Base stream URL, e.g. http://localhost:4000/sse/stream */
  url: string;
  tenantId: string;
  handlers?: SSEHandlers;
  /** Called whenever the connection status changes (drives a "live/reconnecting" chip). */
  onStatus?: (status: SSEStatus) => void;
  /**
   * EventSource factory. Defaults to the browser's native `EventSource`.
   * Inject a fake in tests / SSR-guard environments.
   */
  eventSourceFactory?: EventSourceFactory;
  /** Reconnect backoff schedule in ms (capped exponential). */
  backoffMs?: number[];
}

const DEFAULT_BACKOFF = [500, 1000, 2000, 5000, 10000];

export interface SSEClient {
  status(): SSEStatus;
  close(): void;
}

function defaultFactory(url: string): EventSourceLike {
  if (typeof EventSource === 'undefined') {
    throw new Error(
      'EventSource is not available in this environment; pass eventSourceFactory.',
    );
  }
  return new EventSource(url) as unknown as EventSourceLike;
}

/**
 * Open a typed, auto-reconnecting SSE connection. Returns a handle to read
 * status and close it. Reconnect attempts walk the backoff schedule and then
 * hold at its last value until a successful (re)open resets them.
 */
export function createSSEClient(opts: SSEClientOptions): SSEClient {
  const factory = opts.eventSourceFactory ?? defaultFactory;
  const backoff = opts.backoffMs ?? DEFAULT_BACKOFF;
  const url = withTenant(opts.url, opts.tenantId);

  let es: EventSourceLike | null = null;
  let status: SSEStatus = 'connecting';
  let attempt = 0;
  let closed = false;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  const setStatus = (s: SSEStatus) => {
    status = s;
    opts.onStatus?.(s);
  };

  const bind = () => {
    es = factory(url);

    for (const name of SSE_EVENT_NAMES) {
      const handler = opts.handlers?.[name];
      es.addEventListener(name, (ev: MessageEvent) => {
        if (!handler) return;
        try {
          const data = JSON.parse(ev.data);
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          (handler as (d: any) => void)(data);
        } catch {
          // Malformed frame — drop it rather than tearing down the stream.
        }
      });
    }

    es.onopen = () => {
      attempt = 0;
      setStatus('open');
    };

    es.onerror = () => {
      // Native EventSource retries on its own, but we manage backoff explicitly
      // so status is observable and the schedule is bounded/testable.
      if (closed) return;
      try {
        es?.close();
      } catch {
        /* noop */
      }
      es = null;
      setStatus('reconnecting');
      scheduleReconnect();
    };
  };

  const scheduleReconnect = () => {
    if (closed || reconnectTimer) return;
    const delay = backoff[Math.min(attempt, backoff.length - 1)];
    attempt += 1;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      if (closed) return;
      bind();
    }, delay);
  };

  setStatus('connecting');
  bind();

  return {
    status: () => status,
    close: () => {
      closed = true;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      try {
        es?.close();
      } catch {
        /* noop */
      }
      es = null;
      setStatus('closed');
    },
  };
}

/** Append/merge the required `tenantId` query param onto a stream URL. */
export function withTenant(url: string, tenantId: string): string {
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}tenantId=${encodeURIComponent(tenantId)}`;
}
