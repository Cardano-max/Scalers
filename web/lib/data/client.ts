/**
 * urql GraphQL client factory for the kkg.4 gateway. SSE (not GraphQL
 * subscriptions) carries realtime — see `sse.ts` — so this client only needs
 * the default fetch exchange for queries + mutations. The tenant is carried in
 * variables (single active tenant; backend multi-tenant via RLS).
 */
import { Client, cacheExchange, fetchExchange } from 'urql';

export interface ClientConfig {
  graphqlUrl: string;
}

export function createGraphQLClient(config: ClientConfig): Client {
  return new Client({
    url: config.graphqlUrl,
    exchanges: [cacheExchange, fetchExchange],
    // requestPolicy left at default cache-first; screens can override per query.
  });
}
