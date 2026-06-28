<!-- GSD:project-start source:PROJECT.md -->
## Project

**Scalers**

Scalers is an internal, single-client, engine-first **agentic social-media marketing system**. Three engines run on one deterministic harness plus a shared deep-research engine: organic Instagram/Facebook **posting**, Gmail cold **outreach**, and comment/DM **engagement**. A generic, professional **Operator Console** (locked design) is the human-in-the-loop surface; the client niche (currently a tattoo-artist studio) lives only in backend per-tenant config / "packs".

**Core Value:** Reliable, auditable marketing autonomy: the engine does the work and only escalates the uncertain few — it **never fires off-brand or unsafe content** without operator sign-off, and every action is explainable (jury confidence, gates, idempotency).

### Constraints

- **Tech stack**: Python deterministic harness (hand-coded graph; DBOS durable substrate; BAML/Pydantic-AI typed cells); NestJS 11 code-first GraphQL + `@Sse()` gateway; FastAPI engine surface (AG-UI-shaped SSE frames); Next.js Operator Console (locked). Postgres+pgvector, Redis, MinIO. — operator-directed + June-2026 vetted.
- **Models**: Claude Opus 4.8 drafting (Fable 5 for hardest strategy, with server-side fallback); Gemini 3.1 Flash-Lite / Haiku 4.5 classification; cross-family jury (Opus + GPT-5.5 + Gemini Pro + DeepSeek). — temp-0, pinned.
- **Infra**: all local Docker + Cloudflare tunnel; no AWS. — client provides no cloud.
- **Safety**: exactly-once side effects (no double IG post / Gmail send); enforced at the DB boundary (idempotency keys + unique constraints + outbox) independent of substrate.
- **Quality**: eval-on-every-change; a gold set must exist before scaling (senior-ML critique gate).
- **Timeline**: fast ship, incremental; work nonstop.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:STACK.md -->
## Technology Stack

Technology stack not yet documented. Will populate after codebase mapping or first phase.
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd:quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd:debug` for investigation and bug fixing
- `/gsd:execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd:profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
