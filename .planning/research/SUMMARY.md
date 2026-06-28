# Research Summary: Scalers (June 28 2026)

> Condensed. Full detail + sources + adversarial critique live in the orchestration repo `super/scalers-backend-plan.md` and the stack-finalization workflow output. Pins are current to June 2026 and were verified via live web + context7 + arxiv with a senior-ML critique.

## Stack (vetted)

- **Control:** hand-coded deterministic graph in Python (no framework owns control flow); Graph-Harness invariants (immutable plan-versions, planning/execution/recovery separation, bounded 3-level recovery).
- **Durable substrate:** **DBOS Transact** (v1, Postgres-only, embeds) — exactly-once also enforced at the DB boundary (idempotency keys + unique constraints + outbox). Restate is the heavier alternative if per-session virtual objects are needed later.
- **Typed cells:** **BAML** (Schema-Aligned Parsing) + **Pydantic-AI 2.0** + Pydantic v2 validator bank. No constrained decoding.
- **Models:** Claude **Opus 4.8** drafting (**Fable 5** hardest strategy, server-side fallback); **Gemini 3.1 Flash-Lite / Haiku 4.5** classification; cross-family jury (Opus + **GPT-5.5** + Gemini 3.1 Pro + DeepSeek V4).
- **Confidence/routing:** calibrate-first via **self-consistency variance** (hosted Claude has no logprobs); thresholds calibrated on the gold set.
- **Eval/observability:** **Inspect AI** + **DeepEval** + **RAGAS** + **Arize Phoenix** (OSS/OTel); reliability scorecard + OWASP-Top-10-Agentic red-team.
- **Memory:** Mem0-style hierarchical + adaptive retrieval gating + retention regularization.
- **Tooling:** IG/FB = **mikusnuz/meta-mcp v2.0.1** (Graph API v25.0) + direct FB Pages API in publisher; Gmail = official MCP for read/triage/draft + REST `users.messages.send`; research = **Exa** + **Firecrawl v2**.
- **Gateway/FE connection:** Next.js console ↔ **NestJS 11 code-first GraphQL + `@Sse()`** ↔ **FastAPI** engine (AG-UI-shaped SSE frames). SSE, not WebSocket.
- **Infra:** local Docker (Postgres+pgvector, Redis, MinIO) + Cloudflare tunnel. No AWS.

## Features (table stakes vs differentiators)

- **Table stakes:** typed/validated cells, approval queue with explainable autonomy decision, durable exactly-once publishing, deliverability compliance, eval-on-every-change.
- **Differentiators:** deterministic harness ("never fires off-brand"), cross-family jury + calibrated confidence, booking-free feedback loop, generic console with per-tenant packs.
- **Anti-features:** agent-owns-control-flow, private-API social tooling, tattoo-specific frontend, paid-ads in core.

## Architecture (4 layers)

Control (graph + durable substrate) · Intelligence (typed cells + jury + routing) · Capacity (MCP hands + idempotent publisher + GraphQL/SSE surface) · Knowledge (memory/RAG + KB + feedback + gold sets). FE leverages all via GraphQL queries/mutations + SSE.

## Pitfalls (critique gate)

- **Build the gold set FIRST** — calibration + eval-on-change depend on it (Phase 2).
- Don't bank logprob/probe confidence on hosted models — use self-consistency.
- Don't run a full cross-family jury per realtime reply (latency) — single fast judge + safety classifier for engine 3.
- Start **Meta app review day 1** (2–4 week long pole).
- Set per-run/per-reply latency + cost budgets before scaling.
- Pin Graph API v25.0; plan v26 migration (Sep 2026) + reach→Page Viewer Metric change.

---
*Synthesized: 2026-06-28*
