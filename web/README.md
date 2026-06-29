# Scalers — Operator Console (web)

The operator console: a Next.js (App Router) + TypeScript + CopilotKit/AG-UI app
that renders the engine's real data — review queue, jury cards, activity/reasoning
timeline, runs, live feed, command chat + autonomy dial. It binds to **eng1's
kkg.4 observability read API** (GraphQL queries + SSE).

This is the **Phase-4 FE foundation** (bead `CustomerAcq-45v.1`): the locked app
shell + a typed data layer. The individual screens land on top of it per their
own beads (Review queue `45v.3`, Activity `45v.4`, Live feed `45v.7`, Runs
`45v.5`, Command `45v.9`, Overview `45v.8`).

## Commands

```bash
npm install        # install deps
npm run dev        # dev server (http://localhost:3000)
npm run build      # production build
npm start          # serve the production build
npm test           # vitest (component + data-layer unit tests)
npm run typecheck  # tsc --noEmit
```

## Data layer — mock now, live on flip

The contract (`scalers-backend-plan §1.1–1.4` / `systemdesign §4.4–4.6`) is
typed in `lib/data/models.ts`. Every screen reads/writes through one
`DataAdapter` (`lib/data/adapter.ts`), so the backend is swappable with **one env
var** — no component changes:

```bash
# .env.local
NEXT_PUBLIC_DATA_SOURCE=mock   # default: clearly-labeled in-memory mock (no backend)
# NEXT_PUBLIC_DATA_SOURCE=live  # bind to the real kkg.4 gateway:
NEXT_PUBLIC_GRAPHQL_URL=http://localhost:4000/graphql
NEXT_PUBLIC_SSE_URL=http://localhost:4000/sse/stream
NEXT_PUBLIC_TENANT_ID=northwind
```

- **mock** → `lib/data/mock-adapter.ts` — kkg.4-shaped seed data (Northwind /
  HVAC pack), a timer-driven SSE feed. Lets the console render before kkg.4
  ships. **Never** fabricates data inside components — everything flows through
  the typed models, exactly as the live path will.
- **live** → `lib/data/live-adapter.ts` — urql for GraphQL queries/mutations +
  native `EventSource` for the 7 canonical SSE events, auto-reconnecting.

When eng1's `/graphql` + `/sse` are live, set `NEXT_PUBLIC_DATA_SOURCE=live` and
point the URLs at the gateway. Done.

## Layout

```
app/            # Next App Router — layout, providers, the single-page shell entry, tokens (globals.css)
components/     # AppShell, Sidebar, TopBar, HarnessStatusCard, states, screens registry, SmokeScreen, icons
lib/data/       # models, queries, urql client, sse client, adapter interface + mock/live adapters, DataProvider
lib/            # fonts, tokens (runtime color maps), useAsync
state/          # console-store (active-screen nav + edit-reset)
```

## Safety (439 HOLD)

The console **shows** autonomy state but **never** enables auto while the 439
HOLD is active — the dial is display-or-request-only; enabling auto is
backend-gated (eval + calibration must pass first). The mock adapter mirrors the
gate: a held channel cannot be switched to `AUTO`. Approvals **resume** the
engine; they never bypass a gate.

## Design

Recreated from the locked design handoff. Two semantic accents drive everything:
**teal = automation / healthy**, **amber = human-in-the-loop / escalated**.
Tokens are verbatim in `app/globals.css`. Entrance motion is **transform-only**
(content is never gated behind `opacity:0`).
