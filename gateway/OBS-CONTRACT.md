# OBS-04 Observability read API — contract (kkg.4)

The contract eng5 binds its data layer to. **Stable**: field names below are what the
gateway emits; the impl flips mock→live behind them. Code-first GraphQL (`@nestjs/graphql`)
served at `POST /graphql`; SSE at `GET /sse/*`. Every query is **tenant-scoped** (a
`tenantId` arg; scoping flows through `runs.tenant_id`).

## GraphQL SDL

```graphql
# ── leaf / value types ──────────────────────────────────────────────
type Span {
  spanId: ID!
  runId: ID!
  parentSpanId: ID
  node: String!            # graph node / cell / gate / tool name
  kind: String!            # node | cell | gate | tool
  startTs: String
  endTs: String
  durationMs: Float
  input: String            # truncated to 2000 chars
  output: String           # truncated to 2000 chars
  inputTruncated: Boolean!
  outputTruncated: Boolean!
  status: String!          # ok | failed
  error: String
  children: [Span!]!       # nested spans (trajectory tree)
}

type JudgeVote {
  judge: String!
  family: String           # anthropic | openai | google | deepseek
  voice: Float!            # [0,1]
  safety: Float!
  appr: Float!
  overall: Float!          # (voice+safety+appr)/3
}

type Gate { label: String!, ok: Boolean! }

type Approval {            # the jury card (one per autonomy decision)
  decisionId: ID!
  runId: ID!
  channel: String!
  actionKind: String!
  jury: [JudgeVote!]!
  pooledConfidence: Float!
  threshold: Float!
  agreement: Float!
  gates: [Gate!]!
  safetyVerdict: String!   # pass | flag | veto
  decision: String!        # auto | review | regenerate
  escKind: String!         # none | gate | safety | split | degraded | below_threshold | mode
  escLabel: String!
  createdAt: String!
}

type DeepLink { url: String, externalId: String, threadRef: String }   # url null = link disabled
type ThreadMessage { role: String!, name: String, text: String! }      # role: in | out
type Comment { name: String!, text: String!, autoReplied: Boolean! }
type Metric { label: String!, value: Float! }
type Engagement { thread: [ThreadMessage!]!, comments: [Comment!]!, metrics: [Metric!]! }

type SideEffect {
  idempotencyKey: ID!
  channel: String!
  status: String!          # PENDING | SENDING | SENT | FAILED
  providerId: String
  deepLink: DeepLink       # null when the provider returned no URL
  engagement: Engagement!
}

type Run {                 # trajectory spans + autonomy split + idem/retries
  runId: ID!
  tenantId: String!
  type: String!            # posting | outreach | engagement
  trigger: String!         # manual | schedule
  status: String!          # running | completed | failed | needs-review
  autoCount: Int!
  reviewCount: Int!
  retries: Int!
  createdAt: String
  spans: [Span!]!          # structured thinking-spans (dur + I/O)
  decisions: [Approval!]!  # per-action jury/decision
  sideEffects: [SideEffect!]!
}

type Kpis {                # overview tiles
  runs: Int!
  autoCount: Int!
  reviewCount: Int!
  retries: Int!
  sideEffects: Int!
}

type FeedEvent {           # scoped event history; each carries runId + expands to its trace
  id: ID!
  runId: ID
  tenantId: String!
  kind: String!            # run | decision | action | feed
  text: String!
  at: String!
  severity: String!        # info | warn | error
  run: Run                 # lazy-expand to the full trace (null when runId is null)
}

type Query {
  runs(tenantId: String!, limit: Int = 50, offset: Int = 0): [Run!]!
  run(tenantId: String!, runId: ID!): Run
  approval(tenantId: String!, decisionId: ID!): Approval
  kpis(tenantId: String!): Kpis!
  feed(tenantId: String!, limit: Int = 100, offset: Int = 0): [FeedEvent!]!
}
```

## SSE — 7 canonical events (systemdesign §4.5)

`GET /sse/stream?tenantId=...` multiplexes the stream; each SSE frame is
`{ event: <name>, data: <json> }`. Reconnect via `Last-Event-ID` (lazy-expand large traces
by querying `run(runId)` on demand).

| event | data shape | drives |
|-------|-----------|--------|
| `feed.event` | `FeedEvent` | live feed + overview preview |
| `action.created` | `SideEffect` | new escalation → review queue/badge |
| `action.updated` | `SideEffect` | approved/sent/rejected/regenerated |
| `run.updated` | `Run` | run progress / trajectory |
| `kpi.updated` | `Kpis` | overview KPI tiles |
| `health.updated` | `{ db: String, gateway: String }` | system-health rows |
| `toast` | `{ text: String, severity: String }` | toast notifications |

Also `GET /sse/feed?tenantId=...` emits `feed.event` frames only (the feed timeline).

## Binding notes for eng5
- Flip mock→live by pointing the GraphQL client at `POST /graphql` and the `EventSource` at `GET /sse/stream?tenantId=…`.
- `tenantId` is required on every query; seed/demo tenant is `ink-studio`.
- `FeedEvent.run` is the expand-to-trace edge — query `run` fields under it only when a row is expanded (lazy).
