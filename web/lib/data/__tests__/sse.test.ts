import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  createSSEClient,
  SSE_EVENT_NAMES,
  withTenant,
  type EventSourceLike,
  type SSEStatus,
} from '../sse';

/** Fake EventSource capturing listeners so tests can drive frames + failures. */
class FakeEventSource implements EventSourceLike {
  url: string;
  listeners = new Map<string, (ev: MessageEvent) => void>();
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
  }
  addEventListener(type: string, listener: (ev: MessageEvent) => void) {
    this.listeners.set(type, listener);
  }
  close() {
    this.closed = true;
  }
  emit(type: string, data: unknown) {
    this.listeners.get(type)?.({ data: JSON.stringify(data) } as MessageEvent);
  }
  triggerOpen() {
    this.onopen?.(new Event('open'));
  }
  triggerError() {
    this.onerror?.(new Event('error'));
  }
}

describe('withTenant', () => {
  it('appends tenantId, respecting an existing query string', () => {
    expect(withTenant('http://x/sse/stream', 'northwind')).toBe(
      'http://x/sse/stream?tenantId=northwind',
    );
    expect(withTenant('http://x/sse/stream?foo=1', 'a b')).toBe(
      'http://x/sse/stream?foo=1&tenantId=a%20b',
    );
  });
});

describe('createSSEClient', () => {
  let made: FakeEventSource[];
  const factory = (url: string) => {
    const es = new FakeEventSource(url);
    made.push(es);
    return es;
  };

  beforeEach(() => {
    made = [];
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('dispatches all 7 canonical events to typed handlers', () => {
    const seen: Record<string, unknown> = {};
    const client = createSSEClient({
      url: 'http://x/sse/stream',
      tenantId: 't1',
      eventSourceFactory: factory,
      handlers: {
        'feed.event': (d) => (seen['feed.event'] = d),
        'action.created': (d) => (seen['action.created'] = d),
        'action.updated': (d) => (seen['action.updated'] = d),
        'run.updated': (d) => (seen['run.updated'] = d),
        'kpi.updated': (d) => (seen['kpi.updated'] = d),
        'health.updated': (d) => (seen['health.updated'] = d),
        toast: (d) => (seen['toast'] = d),
      },
    });
    const es = made[0];
    es.triggerOpen();
    for (const name of SSE_EVENT_NAMES) {
      es.emit(name, { name });
    }
    expect(Object.keys(seen).sort()).toEqual([...SSE_EVENT_NAMES].sort());
    expect(seen['kpi.updated']).toEqual({ name: 'kpi.updated' });
    client.close();
  });

  it('reports open status and includes tenantId in the URL', () => {
    const statuses: SSEStatus[] = [];
    const client = createSSEClient({
      url: 'http://x/sse/stream',
      tenantId: 'northwind',
      eventSourceFactory: factory,
      onStatus: (s) => statuses.push(s),
    });
    made[0].triggerOpen();
    expect(made[0].url).toContain('tenantId=northwind');
    expect(statuses).toContain('connecting');
    expect(statuses).toContain('open');
    client.close();
    expect(statuses).toContain('closed');
  });

  it('auto-reconnects on error per the backoff schedule', () => {
    const statuses: SSEStatus[] = [];
    const client = createSSEClient({
      url: 'http://x/sse/stream',
      tenantId: 't1',
      eventSourceFactory: factory,
      backoffMs: [100, 200],
      onStatus: (s) => statuses.push(s),
    });
    made[0].triggerOpen();
    expect(made.length).toBe(1);

    // drop -> reconnecting, then a new EventSource after the first backoff step
    made[0].triggerError();
    expect(statuses).toContain('reconnecting');
    expect(made[0].closed).toBe(true);

    vi.advanceTimersByTime(100);
    expect(made.length).toBe(2); // reconnected

    // second drop walks to the next backoff value
    made[1].triggerError();
    vi.advanceTimersByTime(200);
    expect(made.length).toBe(3);

    client.close();
  });

  it('does not reconnect after close()', () => {
    const client = createSSEClient({
      url: 'http://x/sse/stream',
      tenantId: 't1',
      eventSourceFactory: factory,
      backoffMs: [100],
    });
    made[0].triggerOpen();
    client.close();
    made[0].triggerError(); // after close, ignored
    vi.advanceTimersByTime(1000);
    expect(made.length).toBe(1);
  });

  it('survives a malformed frame without tearing down', () => {
    let got: unknown;
    const client = createSSEClient({
      url: 'http://x/sse/stream',
      tenantId: 't1',
      eventSourceFactory: factory,
      handlers: { 'feed.event': (d) => (got = d) },
    });
    const es = made[0];
    // malformed JSON -> dropped silently
    es.listeners.get('feed.event')?.({ data: '{bad json' } as MessageEvent);
    expect(got).toBeUndefined();
    // valid frame still delivered
    es.emit('feed.event', { id: 'feed_1' });
    expect(got).toEqual({ id: 'feed_1' });
    client.close();
  });
});
