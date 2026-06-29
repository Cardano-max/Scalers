/**
 * Live DataAdapter — binds to the real kkg.4 gateway via urql (queries +
 * mutations) and native EventSource (SSE). Field names are stable per eng1, so
 * the documents in `queries.ts` resolve against the gateway unchanged. Flip
 * NEXT_PUBLIC_DATA_SOURCE=live to use this in place of the mock adapter.
 */
import type { Client } from 'urql';
import { createGraphQLClient } from './client';
import { createSSEClient, type SSEClient, type SSEHandlers, type SSEStatus } from './sse';
import * as Q from './queries';
import type { DataAdapter } from './adapter';
import type {
  Action,
  AutonomyConfig,
  AutonomyMode,
  Channel,
  ActionFilter,
  ChatMessage,
  EngineState,
  FeedEvent,
  FeedFilter,
  Overview,
  Run,
  RunFilter,
  SystemHealth,
  Tenant,
} from './models';

export interface LiveConfig {
  graphqlUrl: string;
  sseUrl: string;
}

export class LiveAdapter implements DataAdapter {
  readonly source = 'live' as const;
  private client: Client;
  private sseUrl: string;

  constructor(config: LiveConfig) {
    this.client = createGraphQLClient({ graphqlUrl: config.graphqlUrl });
    this.sseUrl = config.sseUrl;
  }

  private async query<T>(doc: string, vars: Record<string, unknown>): Promise<T> {
    const res = await this.client.query<T>(doc, vars).toPromise();
    if (res.error) throw res.error;
    return res.data as T;
  }

  private async mutate<T>(doc: string, vars: Record<string, unknown>): Promise<T> {
    const res = await this.client.mutation<T>(doc, vars).toPromise();
    if (res.error) throw res.error;
    return res.data as T;
  }

  getTenant(id: string) {
    return this.query<{ tenant: Tenant | null }>(Q.TENANT_QUERY, { id }).then(
      (d) => d.tenant,
    );
  }
  getOverview(tenantId: string) {
    return this.query<{ overview: Overview }>(Q.OVERVIEW_QUERY, { tenantId }).then(
      (d) => d.overview,
    );
  }
  getReviewQueue(tenantId: string, filter?: ActionFilter) {
    return this.query<{ reviewQueue: Action[] }>(Q.REVIEW_QUEUE_QUERY, {
      tenantId,
      filter: filter ?? null,
    }).then((d) => d.reviewQueue);
  }
  getAction(id: string) {
    return this.query<{ action: Action | null }>(Q.ACTION_QUERY, { id }).then(
      (d) => d.action,
    );
  }
  getRuns(tenantId: string, filter?: RunFilter) {
    return this.query<{ runs: Run[] }>(Q.RUNS_QUERY, {
      tenantId,
      filter: filter ?? null,
    }).then((d) => d.runs);
  }
  getRun(id: string) {
    return this.query<{ run: Run | null }>(Q.RUN_QUERY, { id }).then((d) => d.run);
  }
  getFeed(tenantId: string, filter?: FeedFilter, after?: string, limit?: number) {
    return this.query<{ feed: FeedEvent[] }>(Q.FEED_QUERY, {
      tenantId,
      filter: filter ?? null,
      after: after ?? null,
      limit: limit ?? null,
    }).then((d) => d.feed);
  }
  getSystemHealth(tenantId: string) {
    return this.query<{ systemHealth: SystemHealth }>(Q.SYSTEM_HEALTH_QUERY, {
      tenantId,
    }).then((d) => d.systemHealth);
  }

  subscribe(
    tenantId: string,
    handlers: SSEHandlers,
    onStatus?: (s: SSEStatus) => void,
  ): SSEClient {
    return createSSEClient({ url: this.sseUrl, tenantId, handlers, onStatus });
  }

  approveAction(id: string, idempotencyKey: string) {
    return this.mutate<{ approveAction: Action }>(Q.APPROVE_ACTION, {
      id,
      idempotencyKey,
    }).then((d) => d.approveAction);
  }
  rejectAction(id: string, reason?: string) {
    return this.mutate<{ rejectAction: Action }>(Q.REJECT_ACTION, {
      id,
      reason: reason ?? null,
    }).then((d) => d.rejectAction);
  }
  editActionDraft(id: string, draft: string) {
    return this.mutate<{ editActionDraft: Action }>(Q.EDIT_ACTION_DRAFT, {
      id,
      draft,
    }).then((d) => d.editActionDraft);
  }
  regenerateAction(id: string) {
    return this.mutate<{ regenerateAction: Action }>(Q.REGENERATE_ACTION, {
      id,
    }).then((d) => d.regenerateAction);
  }
  setEngineState(tenantId: string, paused: boolean) {
    return this.mutate<{ setEngineState: EngineState }>(Q.SET_ENGINE_STATE, {
      tenantId,
      paused,
    }).then((d) => d.setEngineState);
  }
  setAutonomy(
    tenantId: string,
    channel: Channel,
    mode: AutonomyMode,
    threshold: number,
  ) {
    return this.mutate<{ setAutonomy: AutonomyConfig }>(Q.SET_AUTONOMY, {
      tenantId,
      channel,
      mode,
      threshold,
    }).then((d) => d.setAutonomy);
  }
  sendCommand(tenantId: string, text: string) {
    return this.mutate<{ sendCommand: ChatMessage }>(Q.SEND_COMMAND, {
      tenantId,
      text,
    }).then((d) => d.sendCommand);
  }
}
