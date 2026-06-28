# Scalers Spec

The "what" document — requirements, behaviors, acceptance criteria. Canonical stack: `docs/stack-decision.md`. Deep detail: `super/scalers-backend-plan.md` + Scalers repo `.planning/`. Owner: super.

---

## 1. Core Model

Scalers is a **deterministic agentic harness**: a fixed control graph in code where the LLM runs only inside bounded, schema-typed cells. Three engines (Posting, Outreach, Engagement) share one harness + one deep-research engine. Every action produced is scored by a computed confidence + a cross-family jury + deterministic gates, then **routed** (pure code) to one of: auto-execute, human-review (Operator Console), or regenerate. A generic Operator Console is the human-in-the-loop surface; the client niche lives only in per-tenant config ("packs").

**Invariant:** the model never decides control flow; raw model text never flows downstream (always parser-validated); side effects fire exactly once.

## 2. Components

- **Engine (Python, FastAPI):** LangGraph spine (topology fixed in code) + Postgres checkpointer; Pydantic-AI typed cells (~8 expert cells); deterministic nodes (aggregator/scorer, confidence computer, validator bank, router, publisher, feedback); exactly-once side-effect boundary (idempotency keys + Postgres unique constraints + outbox).
- **Autonomy:** cross-family jury (Opus 4.8 + GPT-5.5 + Gemini 3.1 Pro + local Ollama juror, since client provides only an Anthropic key) → reliability-weighted aggregation in code; deterministic gates (suppression, rate cap, PII, tenant policy, media format); independent safety classifier; per-channel autonomy dial.
- **Knowledge:** Postgres + pgvector KB (past posts + performance per tenant), Mem0-style memory with drift control, brand-voice grounding, feedback loop.
- **Tooling (MCP "hands"):** self-hosted Meta MCP (mikusnuz/meta-mcp, Graph API v25.0) + direct FB Pages API; Gmail MCP (read/triage/draft) + REST send; research = Exa + Firecrawl v2 + free Meta Ad Library.
- **Gateway (NestJS 11):** code-first GraphQL (queries + mutations) + `@Sse()` live feed; thin BFF.
- **Console (Next.js + CopilotKit/AG-UI):** locked, generic design — Overview, Review queue, Live feed, Runs, Command.
- **Infra:** local Docker (Postgres+pgvector, Redis, MinIO) + Cloudflare tunnel. No AWS.

## 3. Behaviors (input → output per engine)

- **Posting:** trigger (schedule/command) → research → strategy → create (draft + media) → validate/format → score+route → (auto) publish to IG/FB via Meta MCP / (review) Operator Console → engage. Output: a published or queued post with an audit trail.
- **Outreach:** lead intake → dedupe + suppression → per-prospect research → personalized draft (voice) → deliverability QA → score+route → send via Gmail under warmup/caps → reply/bounce watch → capped follow-up. Output: sent emails + sequence state.
- **Engagement:** Meta webhook (deduped on event id) → history-aware triage (classifier) → draft reply → score+route. **Comments may auto-reply within threshold; DMs always route to a human.** Output: replies + escalations.
- **Console:** GraphQL queries render Overview/Review/Runs; mutations (approve/edit/regenerate/reject, setEngineState, setAutonomy, sendCommand) drive the engine (approve resumes the LangGraph human-in-the-loop interrupt); SSE streams the live feed + run progress + KPIs.

## 4. Data Model

Postgres (JSONB) status/run-state store with append-only `steps[]` per run (feeds Runs/Overview); LangGraph checkpoints in Postgres; pgvector KB (embeddings of past content + metrics, per tenant); per-tenant config tables (autonomy mode/threshold per channel, voice ref, channels, suppression, schedule, limits); outbox table for exactly-once side effects; Redis for the queue + per-tenant scheduler; MinIO for creatives. GraphQL types per `super/scalers-backend-plan.md` §1.

## 5. Constraints

**Operator policy:** comments auto / DMs → human; Reddit out of MVP; competitor ads via free Meta Ad Library; Meta app review submitted day one.

**Email (engine 2 — fresh dedicated domain; full numbers in the config-numbers research):**
- Warmup ramp: ~8/day (wk1) → 18 → 28 → 40/day (wk4), full by wk5–7; +5–10/inbox/week.
- Steady cap: 40–50 cold/inbox/day (Workspace; default 40); 25/day on consumer Gmail. Hard system caps 2000 / 500 are NOT targets.
- Send spacing 90–300s randomized; spread over an 8–11am window in the **recipient's** timezone; Tue–Thu (Wed best).
- Follow-ups: 4 touches, widening gaps (day 0 / +3 / +5 / +7); hard-stop on reply/bounce/unsubscribe.
- Auth: SPF + DKIM (2048-bit) + DMARC (p=quarantine); RFC 8058 one-click unsubscribe; honor opt-out ≤2 days; suppression checked before every send.
- Thresholds: spam complaints **<0.10%** (operate <0.08%), bounce **<2%**.

**Social (engines 1 & 3):** IG content publishing **query `content_publishing_limit` at runtime, default conservative 25/24h** (Reels/stories share the bucket); ~200 Graph calls/hr/account; Reels 9:16, 5–90s, JPEG images. IG DM auto-reply only inside the user-opened 24h window (no HUMAN_AGENT automation), ~200 auto-DMs/hr cap, 1 DM/user/24h on triggers — else human. Pin Graph API v25.0 (plan v26 Sep 2026; reach→Page Viewer Metric June 2026).

**Harness:** typed cells (no raw text downstream); pure-code routing; temp-0 decisions; pinned models; exactly-once side effects; eval-on-every-change; **gold set required before scaling**; confidence via self-consistency (no logprobs on hosted Claude).

**Acceptance (measurable targets):** brand-voice ≥90% on-voice (blind 100-post holdout, ≥2 raters, κ≥0.6); classify/extract P/R ≥0.95; reply safety 0 violations on red-team, <15% auto-drafts need edit; validator ≥99% after retry; ECE ≤0.05; email complaints <0.10%; per-run + per-reply p95 latency and $/post + $/1k-comments budgets defined.

---
*Owner: super · Last updated: 2026-06-28 · aligned to docs/stack-decision.md*
