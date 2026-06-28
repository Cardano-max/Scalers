# Scalers System Design

The **HOW** document. Architecture, packages, interfaces, build order. Hard cap: 5000 lines.

> **Canonical stack:** [`docs/stack-decision.md`](./stack-decision.md) (operator-authored). This doc never re-picks the stack; where a tool is named here it is because the stack decision named it. **WHY** lives in [`docs/prd.md`](./prd.md), **WHAT** in [`docs/spec.md`](./spec.md), **WHEN/WHO** in [`docs/roadmap.md`](./roadmap.md).
>
> **Cross-refs:** the FE-derived GraphQL schema + SSE events come from [`super/scalers-backend-plan.md`](../super/scalers-backend-plan.md) §1; the phased requirement mapping comes from `eng1/src/.planning/` (ROADMAP.md, REQUIREMENTS.md, PROJECT.md). Where this doc and the backend plan's §3 differ on tooling, **stack-decision.md wins** (e.g. typed cells = Pydantic-AI, evals = Langfuse, durability = LangGraph Postgres checkpointer).

---

## 0. Architecture overview — the four layers

Scalers is an internal, single-client, engine-first agentic marketing system: three engines (organic **posting**, Gmail **outreach**, comment/DM **engagement**) on one deterministic harness, plus a shared deep-research engine, fronted by a locked, generic Operator Console.

The system divides into four horizontal layers. Each layer has one owning module and a one-directional dependency: **Capacity → Control → Intelligence → Knowledge**. Nothing below calls up.

```
┌─────────────────────────────────────────────────────────────────────────┐
│ CAPACITY  — operator-facing surface + realtime relay                      │
│   web (Next.js + CopilotKit/AG-UI)  ──HTTP/SSE──▶  gateway (NestJS GraphQL │
│   + @Sse())                                                                │
│   Owns: the 5 console screens, GraphQL read/write contract, SSE fan-out.   │
└───────────────────────────────────┬───────────────────────────────────────┘
                                     │ command queue (Redis stream) ↓
                                     │ event bus    (Redis pub/sub) ↑
┌───────────────────────────────────┴───────────────────────────────────────┐
│ CONTROL   — the deterministic harness (the spine)                          │
│   engine (Python + FastAPI + LangGraph + Postgres checkpointer)            │
│   Owns: the fixed graph, node execution, durability/resume, the            │
│   exactly-once side-effect boundary, the pure-code router.                  │
│   Harness law: the model never picks the next step; the graph is in code.   │
└───────────────────────────────────┬───────────────────────────────────────┘
                                     │ calls bounded cells ↓
┌───────────────────────────────────┴───────────────────────────────────────┐
│ INTELLIGENCE — bounded LLM cells + autonomy                                 │
│   Pydantic-AI typed cells · cross-family jury · deterministic gates ·       │
│   safety classifier · confidence (self-consistency) · validator bank.      │
│   Every cell returns a parser-validated typed schema. No raw model text     │
│   flows downstream. Scoring + routing are pure Python.                      │
└───────────────────────────────────┬───────────────────────────────────────┘
                                     │ reads/writes ↓
┌───────────────────────────────────┴───────────────────────────────────────┐
│ KNOWLEDGE — state, memory, assets                                          │
│   Postgres 16 (+ pgvector: state, append-only audit, checkpoints, vectors) │
│   · Redis (queue + pub/sub + per-artist scheduler) · MinIO (creatives).    │
│   Self-hosted MCP connectors (Meta, Gmail) + research (Firecrawl/Exa).     │
└───────────────────────────────────────────────────────────────────────────┘
```

**Why this split.** The brain stays in Python (LangGraph is the strongest stateful-workflow framework and the LLM ecosystem is Python-native), while the operator-facing API stays in the NestJS/GraphQL/SSE stack the console is built against — without forcing the engine into TypeScript. Redis is the only thing between them: a command stream in, an event bus out. This is the seam that lets `web`, `gateway`, and `engine` be built and deployed independently.

**Runtime.** Everything is one local Docker Compose stack reached through a Cloudflare tunnel (no open port, no AWS). The tunnel exposes exactly two things publicly: the portal and the Meta webhook.

---

## 1. Module structure

Three top-level modules, one per layer boundary that crosses a language. They share no code; they share **contracts** (the GraphQL SDL, the SSE event shapes, the Redis command/event envelopes).

```
scalers/                      (repo: Cardano-max/Scalers)
├─ engine/                    Python — Control + Intelligence + Knowledge access
│   ├─ harness/               the fixed graph, node protocol, checkpointer, router
│   ├─ cells/                 Pydantic-AI typed LLM cells + validator bank
│   ├─ autonomy/             jury · gates · safety classifier · confidence computer
│   ├─ sideeffects/          the exactly-once boundary (idempotency + outbox)
│   ├─ engines/              posting / outreach / engagement graphs (built on harness)
│   ├─ research/             pluggable Firecrawl/Exa adapters
│   ├─ knowledge/            pgvector KB, brand-voice grounding, feedback loop
│   ├─ connectors/           Meta MCP + Gmail MCP clients (no raw API in cells)
│   ├─ api/                  FastAPI: webhook intake + internal command intake + SSE out
│   ├─ bus/                  Redis stream consumer + pub/sub publisher
│   └─ config/              per-tenant packs (generic FE, niche here)
│
├─ gateway/                   TypeScript — Capacity (API)
│   ├─ schema/               code-first GraphQL types (@ObjectType/@Resolver)
│   ├─ resolvers/            queries (read models) + mutations (enqueue commands)
│   ├─ sse/                  @Sse() routes; subscribe Redis pub/sub → relay to FE
│   ├─ readmodel/            Postgres read-model (serving/"gold") access
│   └─ bus/                  Redis command producer + event subscriber
│
└─ web/                       TypeScript — Capacity (console)
    ├─ app/                   Next.js routes for the 5 screens
    ├─ graphql/               Apollo/urql client (queries + mutations)
    ├─ sse/                   EventSource subscriptions
    └─ copilot/               CopilotKit / AG-UI command surface
```

### 1.1 Dependency graph

```
web ──(GraphQL queries/mutations + SSE)──▶ gateway
gateway ──(Redis command stream)──▶ engine        gateway ──(read-model SQL)──▶ Postgres
engine  ──(Redis pub/sub events)──▶ gateway        engine ──(SQL + pgvector)──▶ Postgres
engine  ──(MCP only)──▶ Meta MCP · Gmail MCP · research
```

- **`web` depends only on `gateway`** (GraphQL + SSE). It never talks to the engine or Redis directly.
- **`gateway` depends on `engine` only through Redis** (command stream out, event bus in) and on Postgres for the read models it serves. It holds **no orchestration logic** — it validates, enqueues, and relays. Thin BFF.
- **`engine` owns all writes to run state** and is the only module that calls MCP connectors. It publishes events; it does not know the FE exists.

### 1.2 Ownership of writes (the one rule that keeps it coherent)

| Data | Written by | Read by |
|------|-----------|---------|
| Run state, actions, feed events, KPIs, health | **engine** | gateway (read models) |
| Operator commands (approve/edit/regenerate/reject/setAutonomy/sendCommand) | **gateway** → Redis stream | engine (consumer) |
| Vector KB, brand-voice index, feedback outcomes | **engine** (knowledge) | engine (cells) |
| Side-effect ledger (outbox + idempotency) | **engine** (sideeffects) | engine only |

The gateway never writes run state; the engine never serves GraphQL. This is what lets the two ship in parallel.

---

## 2. The Control spine — LangGraph + Postgres checkpointer

The harness is a **hand-coded fixed graph**. LangGraph provides the graph runtime and durable checkpointing; it does **not** choose steps. (Harness law: the model never picks the next node.)

### 2.1 The graph

Each engine is a `StateGraph` whose nodes are either **code nodes** (pure Python: scoring, routing, gates, side effects) or **cells** (a single bounded LLM call wrapped by a typed parser). Edges are static or conditional on **computed** values only — never on raw model text.

```
Research ─▶ Strategy ─▶ Create ─▶ Check&Score ─▶ Router ─┬─ auto ──▶ SideEffect ─▶ Done
                                       (gates+jury)       ├─ review ─▶ interrupt() ─▶ (resume) ─▶ SideEffect
                                                          └─ regen ──▶ Create   (bounded retries)
```

The shared shape is **Research → Strategy → Create → Check&Score → Route → {auto-act | human-review | regenerate}**. Engine-specific nodes differ (posting publishes; outreach sends; engagement replies) but the spine is identical and lives in `harness/`.

### 2.2 Durability

- **Checkpointer:** the LangGraph **Postgres checkpointer** persists graph state after every node. A crashed run resumes from the last completed node — no work is re-done and no node is half-applied. (Temporal is deferred; for one client, checkpoints + Postgres are enough — see stack-decision.md.)
- **Checkpoints are save-points, not exactly-once execution.** This is the critical, non-obvious point: a checkpointer can replay a node, so it must never be the thing guaranteeing "post exactly once." That guarantee lives in §3, independently, at the DB boundary.
- The eng planning references **DBOS** as an embeddable durable-step option behind a thin interface; the canonical spine is the LangGraph Postgres checkpointer per stack-decision.md, and the exactly-once guarantee is enforced at the boundary regardless of substrate, so a later swap changes nothing operator-visible.

### 2.3 Human-in-the-loop

Human review is a LangGraph **`interrupt()`**. When the router sends an action to `review`, the graph pauses at the interrupt and the action is persisted (`status: PENDING`) and emitted as `action.created`. The operator's `approveAction` mutation (via gateway → Redis command → engine) **resumes the interrupt** with the decision; the graph continues to the side-effect node. `reject`/`edit`/`regenerate` resume with the corresponding command. This is why the FE never needs polling — the pause lives in the durable graph, not in a request.

### 2.4 Bounded 3-level recovery

Every cell/side-effect runs under bounded recovery: **retry → regenerate/local-patch → human-review**. After N bounded retries a cell escalates rather than looping. Recovery levels are code, not model judgment.

---

## 3. The exactly-once side-effect boundary

The hardest guarantee in the system: **no double IG post, no double Gmail send, no double DM**, even across crash/retry/replay. It is enforced **independently of the orchestration substrate**, at the database, so it holds whether the spine is the LangGraph checkpointer, DBOS, or anything else.

Three mechanisms, all in `engine/sideeffects/`:

1. **Idempotency key** — every side effect carries a deterministic key derived from `(tenant, channel, target, content-hash)`, e.g. `nw:outreach:bayside-pg:c8821`. The same logical action always produces the same key.
2. **Postgres unique constraint** — the side-effect ledger has `UNIQUE(idempotency_key)`. A second attempt to record the same effect fails the insert; the boundary treats a unique-violation as "already done" and returns the prior result instead of re-calling the connector.
3. **Transactional outbox** — the side effect is committed in two phases: (a) within the same DB transaction that advances run state, write an `outbox` row `(idempotency_key, channel, payload, status=PENDING)`; (b) a separate, at-least-once **dispatcher** reads `PENDING` rows, calls the MCP connector, and on success flips the row to `SENT` with the provider id. Because the connector call is keyed and the ledger is unique-constrained, at-least-once dispatch becomes **effectively exactly-once**.

```
node advances run state ─┐ (one DB tx)
                         ├─▶ outbox row PENDING (idempotency_key UNIQUE)
checkpoint committed ────┘
        │
        ▼ (separate dispatcher, at-least-once)
   claim PENDING ─▶ call MCP connector ─▶ on success: SENT + provider_id
                                          on dup key: treat as already SENT
```

**Invariant:** a connector is only ever called by the dispatcher, only for an outbox row, and the row's key is unique. Replaying the graph re-creates the same `PENDING` row (unique → no duplicate) or finds it already `SENT` (skip). The side effect is decoupled from graph progress, so a checkpoint replay can never re-fire it.

This boundary is also where platform caps are enforced (IG `content_publishing_limit`, Gmail warmup/caps, IG DM 24h window): the dispatcher checks the rate gate before calling the connector and re-queues if capped.

---

## 4. The connection contract — FE ↔ gateway ↔ engine

The frontend is **locked** (the generic Operator Console, 5 screens). It is the source of truth for the API contract. Full SDL lives in `super/scalers-backend-plan.md` §1; the canonical shapes are summarized here.

### 4.1 Transport split

- **Operator → server (commands):** GraphQL **mutations** over `POST /graphql`. Client→server only.
- **Server → operator (realtime):** **SSE** over `GET /sse/*` (NestJS `@Sse()`, FE `EventSource`). Server→client only.
- **Reads:** GraphQL **queries** over `POST /graphql`, served from Postgres read models.

**Why SSE, not WebSocket/subscriptions:** the data flow is server→client only (the console *observes* the harness); SSE is simpler, Cloudflare-tunnel friendly, auto-reconnects, and is exactly how AG-UI/CopilotKit stream agent events. Operator actions are mutations, so bidirectional sockets are never needed.

### 4.2 The three hops

```
web ──POST /graphql (query)──────────────────▶ gateway ──SQL──▶ Postgres read model ──▶ data
web ──POST /graphql (mutation +idemKey)──────▶ gateway ──Redis stream (typed command)──▶ engine
engine ──Redis pub/sub (typed event)─────────▶ gateway ──@Sse() frame──▶ web (EventSource)
```

1. **Queries** resolve entirely in the gateway from Postgres read models the engine maintains. The engine is not on the read path.
2. **Mutations** validate in the gateway, then enqueue a **typed command** on a Redis stream. Every mutation carries an `idempotencyKey`. The gateway returns optimistically/acknowledges; the real effect arrives later as an SSE `action.updated`.
3. **Events** the engine emits to Redis pub/sub; the gateway's `@Sse()` route relays them verbatim (reshaped to the FE shape) to the subscribed `EventSource`.

### 4.3 Command screen (AG-UI)

`sendCommand` enqueues a command; the engine streams the assistant reply (tokens + generative-UI events) as **AG-UI-shaped typed frames** over `GET /sse/command?...`. FastAPI emits the frames (`StreamingResponse`); the gateway relays; CopilotKit renders them. This is the one place the engine streams *fine-grained* frames rather than coarse domain events.

### 4.4 GraphQL surface (FE-derived — canonical shapes)

Queries: `tenants`, `tenant`, `overview`, `reviewQueue`, `action`, `runs`, `run`, `feed`, `systemHealth`.
Mutations: `approveAction`, `rejectAction`, `editActionDraft`, `regenerateAction`, `setEngineState`, `setAutonomy`, `sendCommand` (+ future `retryRun`/`cancelRun`).
Core types: `Tenant`, `Action`, `Run`, `RunStep`, `FeedEvent`, `Kpis`, `SystemHealth`, `ChatMessage`, plus the `Escalation`/`JuryDecision`/`Gate` decision sub-objects. Enums: `Channel`, `ActionType`, `Worker`, `ActionStatus`, `EscalationKind`, `AutonomyMode`, `RunTrigger`, `RunStatus`, `Severity`, `Role`. (Field-level definitions: backend-plan §1.1.)

### 4.5 SSE events (canonical names)

Multiplexed on `GET /sse/stream?tenantId=...`:

| event | data | drives |
|-------|------|--------|
| `feed.event` | `FeedEvent` | Live feed + Overview preview |
| `action.created` | `Action` | new escalation → Review queue + badge |
| `action.updated` | `Action` | approved/sent/rejected/regenerated |
| `run.updated` | `Run` \| `RunStep` | run progress / trajectory |
| `kpi.updated` | `Kpis` | Overview KPI cards |
| `health.updated` | `SystemHealth` | System health rows |
| `toast` | `{text, severity}` | toast notifications |

Plus `GET /sse/command?tenantId=...&messageId=...` for the AG-UI streamed reply.

### 4.6 The Redis envelopes (gateway ↔ engine internal contract)

The two modules agree on two envelope shapes (not GraphQL, not FE-facing):

- **Command** (gateway → engine, Redis stream `cmd:{tenantId}`):
  `{ commandId, idempotencyKey, type: "APPROVE_ACTION"|"REJECT_ACTION"|"EDIT_DRAFT"|"REGENERATE"|"SET_ENGINE_STATE"|"SET_AUTONOMY"|"SEND_COMMAND", tenantId, payload }`.
- **Event** (engine → gateway, Redis pub/sub `evt:{tenantId}`):
  `{ eventId, type: "feed.event"|"action.created"|... , tenantId, at, data }` where `data` matches the SSE table above.

The gateway maps GraphQL mutation → Command, and Event → SSE frame. This envelope pair is the only inter-module contract the engine and gateway must hold stable.

---

## 5. Data structures (storage)

All persistent state is in **Postgres 16 + pgvector**. Redis holds transient queue/pub-sub/scheduler state only. MinIO holds binary creatives.

### 5.1 Postgres tables (canonical state + read models)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `tenants` | per-client config + pack | `id`, `name`, `pack`, `engine_state` |
| `autonomy_config` | per-channel dial | `tenant_id`, `channel`, `mode`, `threshold` |
| `runs` | run records (read model + source) | `id`, `tenant_id`, `type`, `trigger`, `status`, `auto_count`, `review_count`, `retries`, `idempotency_key` |
| `run_steps` | trajectory | `run_id`, `at`, `text`, `state` |
| `actions` | review-queue items + decision | `id`, `tenant_id`, `type`, `channel`, `worker`, `draft`, `confidence`, `threshold`, `escalation`, `jury`(jsonb), `gates`(jsonb), `idempotency_key`, `status` |
| `feed_events` | append-only live feed | `id`, `tenant_id`, `worker`, `text`, `at`, `chip`, `severity` |
| `side_effect_ledger` | exactly-once | `idempotency_key` **UNIQUE**, `channel`, `provider_id`, `status`, `result` |
| `outbox` | transactional dispatch | `idempotency_key` **UNIQUE**, `channel`, `payload`, `status`(PENDING/SENT/FAILED), `attempts` |
| `kb_chunks` | vector KB | `tenant_id`, `kind`, `content`, `embedding vector`, `metrics`(jsonb) |
| `checkpoints` | LangGraph Postgres checkpointer | (managed by LangGraph) |

`actions`/`runs`/`feed_events` double as the **read models** the gateway serves — the engine writes them in the same transaction that advances graph state, so a query is always consistent with the last checkpoint.

### 5.2 Redis keyspaces

- `cmd:{tenantId}` — command stream (gateway → engine, consumer group).
- `evt:{tenantId}` — event pub/sub (engine → gateway).
- `sched:{tenantId}` — per-artist posting/follow-up scheduler (sorted set by fire-time).

### 5.3 Per-tenant pack (config, INFRA-04)

The FE is generic; the niche lives here. A pack is a typed config object per tenant: brand-voice skill refs, channel set, autonomy defaults, rate caps, suppression source, sending domain, research sources enabled. Loaded by the engine at run start; never compiled into the FE.

---

## 6. Phase-1 concrete interfaces (eng1 / eng2 / eng3)

Phase 1 = **Foundations & Control core** (HARN-01..06, INFRA-01, INFRA-04). Goal: the deterministic harness runs a typed cell end-to-end with durable, exactly-once execution. These are the **minimal, explicit** interfaces the three engineers implement in parallel. They are defined against the boundaries above so the three streams compose without coordination beyond these signatures.

### 6.1 Split

| Eng | Owns (Phase 1) | Requirements |
|-----|----------------|--------------|
| **eng1** | Control core: the fixed graph + node protocol + Postgres checkpointer wiring + pure-code router | HARN-01, HARN-05, HARN-06 |
| **eng2** | Intelligence: the typed-cell interface (Pydantic-AI) + validator bank | HARN-02, HARN-06 |
| **eng3** | Side-effect boundary + infra: idempotency/outbox + docker-compose + per-tenant pack loader | HARN-03, HARN-04, INFRA-01, INFRA-04 |

### 6.2 eng1 — Control core (`engine/harness/`)

```python
# A node is pure: state in, state out. Code nodes and cells share this type.
class Node(Protocol):
    name: str
    async def __call__(self, state: GraphState) -> GraphState: ...

# The graph is hand-built; edges are static or keyed on COMPUTED fields only.
class Harness:
    def add_node(self, node: Node) -> None: ...
    def add_edge(self, src: str, dst: str) -> None: ...
    def add_conditional(self, src: str, choose: Callable[[GraphState], str]) -> None: ...
    def compile(self, checkpointer: Checkpointer) -> CompiledGraph: ...

# Durable run. resume() continues a graph paused at interrupt() (HITL).
class CompiledGraph(Protocol):
    async def run(self, run_id: str, init: GraphState) -> GraphState: ...
    async def resume(self, run_id: str, decision: Decision) -> GraphState: ...

# Pure-code router (HARN-05): no LLM, temp-0 is irrelevant — it's arithmetic.
def route(confidence: float, threshold: float, gates: list[Gate],
          autonomy: AutonomyMode) -> Literal["auto", "review", "regenerate"]: ...
```

`GraphState` is a Pydantic model carrying `tenant_id`, `run_id`, the working artifact, accumulated signals (`confidence`, `gates`, `jury`), and a `step_log`. The checkpointer is LangGraph's Postgres checkpointer, injected at `compile()`.

### 6.3 eng2 — Typed cells (`engine/cells/`)

```python
# Every LLM call is a Cell. It ALWAYS returns a validated Pydantic model or raises.
# Raw model text never leaves this boundary (HARN-02).
class Cell(Generic[TOut]):
    name: str
    schema: type[TOut]          # Pydantic model the output must satisfy
    model: str                  # PINNED id, e.g. "claude-opus-4-8" (HARN-06)
    temperature: float = 0.0    # temp-0 on decision/classify cells

    async def run(self, prompt: CellInput) -> TOut: ...
    # internally: Pydantic-AI sends schema+prompt, validates on arrival,
    # auto-retries on missing/mistyped field; after N retries -> CellError (no raw text out)

# The validator bank runs deterministic checks a schema can't express
# (banned phrase, claim, length, voice similarity). Pure code, no model.
class Validator(Protocol):
    def check(self, out: BaseModel, ctx: ValidationCtx) -> ValidationResult: ...
```

Phase-1 acceptance: one cell (`Assemble`) runs end-to-end; invalid output is parser-repaired or fails on a code path — it never flows downstream.

### 6.4 eng3 — Side-effect boundary + infra (`engine/sideeffects/`, `engine/config/`, `infra/`)

```python
# The ONLY way a side effect happens. Idempotent by construction (HARN-04).
class SideEffectBoundary(Protocol):
    # Phase 1: enqueue into outbox in the caller's DB tx; returns prior result if key seen.
    async def enqueue(self, key: str, channel: Channel, payload: dict) -> EnqueueResult: ...

# The at-least-once dispatcher. In Phase 1 the connector is a MOCK; the
# exactly-once guarantee is proven against a forced crash/retry (HARN-03/04).
class Dispatcher(Protocol):
    async def dispatch_pending(self) -> None: ...   # claim PENDING -> call connector -> SENT

# Deterministic key derivation — same logical action -> same key.
def idempotency_key(tenant: str, channel: Channel, target: str, content: str) -> str: ...

# Per-tenant pack loader (INFRA-04).
def load_pack(tenant_id: str) -> TenantPack: ...
```

Plus `infra/docker-compose.yml` (INFRA-01) bringing up Postgres+pgvector, Redis, MinIO, and the schema migrations for §5.1 (especially the `UNIQUE(idempotency_key)` constraints the exactly-once test depends on).

### 6.5 Phase-1 integration seam (the end-to-end slice)

The three streams meet on one path that satisfies all four Phase-1 success criteria:

```
load_pack ─▶ Harness[ Research(code) ─▶ Assemble(Cell) ─▶ route(code) ─▶ SideEffect.enqueue ] ─▶ Dispatcher
            └ eng3 ──┘           └ eng2 ──┘        └ eng1 ──┘      └ eng3 ──────┘
```

1. `docker-compose up` → Postgres+pgvector, Redis, MinIO (eng3 / INFRA-01).
2. Graph runs Research→Assemble with one typed cell; bad output repaired-or-fails (eng1 graph + eng2 cell / HARN-01,02).
3. Mock side effect runs exactly once across a forced crash/retry, proven by a unique-constraint test (eng3 / HARN-03,04).
4. Router picks auto/review/regenerate from a confidence input, pure code, temp-0 (eng1 / HARN-05,06).

**Later phases** layer onto these same seams: Phase 4 wires the gateway/web (§4) to the engine's events; Phase 5 fills `autonomy/` (jury/gates/safety/confidence) behind the router input; Phase 6 swaps the mock connector for the real Meta/Gmail MCP behind the unchanged `SideEffectBoundary`. The Phase-1 interfaces are designed not to change when that happens.

---

## 7. Testing strategy

| Boundary | What is tested | How |
|----------|----------------|-----|
| Cell (eng2) | typed output always valid or raises; no raw text downstream | feed malformed model output (recorded/mock) → assert repair or `CellError` |
| Router (eng1) | pure function over (confidence, threshold, gates, mode) | table-driven unit tests; no model in the loop |
| Side-effect boundary (eng3) | **exactly-once under crash/retry** | force a crash between checkpoint and dispatch; assert one ledger row, one connector call (mock counts invocations) |
| Graph (eng1) | resume from last checkpoint; HITL interrupt/resume | kill mid-run, resume, assert no node re-applied; approve resumes interrupt |
| Contract (gateway↔engine) | command/event envelopes round-trip | enqueue command → assert engine consumes; emit event → assert SSE frame shape |
| Contract (web↔gateway) | GraphQL queries/mutations + SSE | resolver tests against read models; EventSource receives relayed events |
| Eval (Phase 2+) | accuracy/F1/ECE vs gold set; eval-on-every-change | Langfuse + Inspect/DeepEval CI gate |

The two highest-value tests are the **exactly-once side-effect test** (the core safety guarantee) and the **cell typed-output test** (the harness-law guarantee). Both are Phase-1 deliverables.

---

## 8. Build order

Per `eng1/src/.planning/ROADMAP.md` — incremental, each phase shippable/demoable. The senior-ML "gold set before scaling" gate is honored by landing the eval spine in Phase 2.

| # | Phase | Outcome | Parallelizable |
|---|-------|---------|----------------|
| 1 | **Foundations & Control core** | harness runs a typed cell end-to-end, durable + exactly-once | eng1 (graph/router) ‖ eng2 (cells) ‖ eng3 (boundary/infra) — meet at §6.5 |
| 2 | Eval spine & gold set | eval-on-every-change vs a real gold set; calibration gates | gold-set authoring ‖ Inspect/DeepEval wiring ‖ pgvector KB scaffold |
| 3 | First vertical slice (posting, mock tooling) | one engine produces a validated post draft into the review queue | research adapter ‖ posting graph ‖ media/format validators |
| 4 | Console + API wiring | locked console on live GraphQL + SSE for the slice | gateway (resolvers/SSE) ‖ web (wire screens) ‖ engine event emission |
| 5 | Autonomy engine | jury + calibrated confidence + gates + safety route auto vs review | jury ‖ confidence/calibration ‖ gates+safety |
| 6 | Real tooling & deliverability | real IG/FB + Gmail through the exactly-once boundary; Meta review day 1 | Meta MCP ‖ Gmail deliverability ‖ tunnel/secrets |
| 7 | Remaining engines, research, knowledge | outreach + engagement + shared research + feedback/memory | outreach ‖ engagement ‖ research/KB |
| 8 | Harden & scale | reliability scorecard + red-team + latency/cost budgets met | scorecard ‖ red-team ‖ budgets |

**Dependency chain:** Phase 1 is the substrate everything else builds on. Phases 1→2→3 are sequential (need the harness, then the eval gate, then a slice to run through it). Phase 4 (console) can start its FE/gateway scaffolding in parallel with Phase 3 since the GraphQL/SSE contract (§4) is already locked, then wire to real events once the slice exists. Phases 5–8 layer onto the unchanged §6 interfaces.

---

*Owner: arch. Aligned to `docs/stack-decision.md`. Cross-refs: `super/scalers-backend-plan.md` §1 (FE contract), `eng1/src/.planning/` (phasing). Next: the Phase-1 ADR (CustomerAcq-dhv.1) formalizes the harness skeleton, module layout, and connection contract decisions recorded here.*
