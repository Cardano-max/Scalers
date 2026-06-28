# Scalers

## What This Is

Scalers is an internal, single-client, engine-first **agentic social-media marketing system**. Three engines run on one deterministic harness plus a shared deep-research engine: organic Instagram/Facebook **posting**, Gmail cold **outreach**, and comment/DM **engagement**. A generic, professional **Operator Console** (locked design) is the human-in-the-loop surface; the client niche (currently a tattoo-artist studio) lives only in backend per-tenant config / "packs".

## Core Value

Reliable, auditable marketing autonomy: the engine does the work and only escalates the uncertain few — it **never fires off-brand or unsafe content** without operator sign-off, and every action is explainable (jury confidence, gates, idempotency).

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Deterministic harness (LLM only in bounded, typed cells; pure-code routing; exactly-once side effects)
- [ ] Autonomy engine (cross-family jury + calibrated confidence + deterministic gates + safety classifier + per-channel dial)
- [ ] Three engines: organic posting, Gmail outreach, comment/DM engagement
- [ ] Shared deep-research engine + knowledge/memory layer (brand-voice, feedback loop)
- [ ] NestJS GraphQL + SSE gateway wiring the locked Operator Console to the live engine
- [ ] Eval spine (gold sets, Inspect AI, calibration + reliability gates, measurable targets)

### Out of Scope

- Booking system / booking-loop — explicitly removed by operator (it was context about the client, not part of this system)
- Multi-tenant SaaS, billing, self-serve onboarding — engine-first, single client for now
- Paid-ads management (official Meta Ads MCP) — optional later module, not core
- Tattoo-specific frontend — the console is generic/professional; niche stays in backend config
- AWS / cloud infra — all local Docker + Cloudflare tunnel
- Private-API social tooling (username/password) — ban risk; official Graph API only

## Context

- The full design conversation lives in `docs/` and the operator's `context.md` (1569 lines, read in full). The frontend is a locked Claude-Design "Operator Console" (5 screens: Overview, Review queue, Live feed, Runs, Command) — currently a mock; this project builds the backend that makes it real.
- Stack was NOT inherited from the design doc (that was authored on old knowledge); it was re-researched and finalized for June 28 2026 with an adversarial senior-ML critique. See `.planning/research/SUMMARY.md` and the authoritative `super/scalers-backend-plan.md` in the orchestration repo.
- Work is coordinated by a multi-agent (initech) fleet and tracked as beads (bd); GSD provides the incremental phase roadmap.

## Constraints

- **Tech stack**: Python deterministic harness (hand-coded graph; DBOS durable substrate; BAML/Pydantic-AI typed cells); NestJS 11 code-first GraphQL + `@Sse()` gateway; FastAPI engine surface (AG-UI-shaped SSE frames); Next.js Operator Console (locked). Postgres+pgvector, Redis, MinIO. — operator-directed + June-2026 vetted.
- **Models**: Claude Opus 4.8 drafting (Fable 5 for hardest strategy, with server-side fallback); Gemini 3.1 Flash-Lite / Haiku 4.5 classification; cross-family jury (Opus + GPT-5.5 + Gemini Pro + DeepSeek). — temp-0, pinned.
- **Infra**: all local Docker + Cloudflare tunnel; no AWS. — client provides no cloud.
- **Safety**: exactly-once side effects (no double IG post / Gmail send); enforced at the DB boundary (idempotency keys + unique constraints + outbox) independent of substrate.
- **Quality**: eval-on-every-change; a gold set must exist before scaling (senior-ML critique gate).
- **Timeline**: fast ship, incremental; work nonstop.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| No agent framework owns control flow | We own the deterministic graph (harness law); framework checkpoints ≠ exactly-once durable execution | ✓ Good |
| Durable substrate = DBOS Transact (v1), not Restate | Postgres-only, embeds, zero extra container = fastest ship; DB-level idempotency is the hard guarantee; swappable later | — Pending |
| Frontend locked & generic | Niche lives in backend config; console is the reliability/approval surface | ✓ Good |
| Gold set before scaling | All calibration + eval-on-change depend on it (critique gate) | — Pending |
| Confidence via self-consistency, not logprobs | Hosted Claude/Gemini expose no logprobs/activations | ✓ Good |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition:** invalidated reqs → Out of Scope; validated reqs → Validated (with phase ref); new reqs → Active; log decisions; update "What This Is" if drifted.

**After each milestone:** full review; Core Value check; audit Out of Scope; update Context.

---
*Last updated: 2026-06-28 after initialization*
