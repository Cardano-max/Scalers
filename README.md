# Scalers

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Cardano-max/Scalers)

**Deploy everything in one click** — the button above reads [`render.yaml`](render.yaml)
and provisions the full stack: pgvector Postgres + the Python engine (Docker,
auto-migrating, persistent artwork disk) + the operator console. It prompts for the
secrets (never committed). Full runbook, the Vercel-console variant, and the
local-Docker/Cloudflare-tunnel path: [`docs/deploy.md`](docs/deploy.md).
*(The button reads the default branch — merge the release PR first.)*

Internal, single-client, engine-first **agentic social-media marketing system**.
Three engines on one deterministic harness + a shared deep-research engine:

1. **Posting** (organic IG/FB): research → strategy → create → check & score → publish → engage
2. **Outreach** (Gmail cold email): intake/suppression → per-prospect research → write → deliverability QA → capped send/follow-up
3. **Engagement** (comments + DMs): Meta webhook → history-aware triage → gated reply

The frontend (Operator Console) is a locked, generic, professional control surface.
The niche lives only in backend per-tenant config / "packs".

## The four backend layers

| Layer | What it is |
|-------|-----------|
| **Control** | hand-coded deterministic graph (skeleton, router, gates, bounded recovery) + durable substrate (exactly-once side effects, HITL pause/resume) |
| **Intelligence** | bounded, typed LLM cells (research, strategist, copywriter, triage, reply) + cross-family jury + confidence-scored routing |
| **Capacity** | MCP "hands" (Meta, Gmail), idempotent publisher, rate governance, concurrency, the GraphQL+SSE surface that powers the console |
| **Knowledge** | memory/RAG, vector KB, brand-voice index, feedback loop, eval gold sets |

## Layout (monorepo)

```
engine/    # Python (FastAPI) — Control + Intelligence + Knowledge core
gateway/   # NestJS 11 — GraphQL + SSE BFF (Capacity exposed to the console)
web/       # Next.js — Operator Console (locked design)
infra/     # docker-compose, Cloudflare tunnel, Postgres/pgvector, Redis, MinIO
evals/     # gold sets + Inspect AI eval suite (eval-on-every-change)
docs/      # architecture + backend build plan
```

Status: bootstrap. Built incrementally via GSD phases; work tracked as beads (bd).
