# docs

In-repo **canonical** project documents. The engine code and `CLAUDE.md` cite these by repo-relative path (e.g. `docs/systemdesign.md §6.2`, `docs/stack-decision.md`), so they live here in the Scalers repo — this directory is the canonical copy going forward. They are synced from the orchestration repo (`CustomerAcq/docs`); edit them there or here and keep the two in step.

## Project documents

| Document | Question | Contains |
|----------|----------|----------|
| [`prd.md`](./prd.md) | **Why** does this exist? | Problem statement, users, success criteria, journeys |
| [`spec.md`](./spec.md) | **What** does this do? | Requirements, behaviors, acceptance criteria |
| [`systemdesign.md`](./systemdesign.md) | **How** does this work? | Architecture (4 layers), packages, interfaces, the §-numbered build order engine code cites |
| [`roadmap.md`](./roadmap.md) | **When/Who** builds what? | Phases, milestones, gates, agent allocation |
| [`stack-decision.md`](./stack-decision.md) | **Which** stack? (operator-authored, canonical) | Verified June-2026 tech stack; pinned model IDs the engine config cites |

## Section references

Source files cite `systemdesign.md` by section number (e.g. `§6.2` = the Phase-1 control-core interfaces, `§3` = the exactly-once side-effect boundary, `§2.2` = the LangGraph checkpointer durability note). Those section numbers are stable in `systemdesign.md`.

## Architecture decisions (ADRs)

Decision records — the fixed shape a phase's work is built to. Each is the dependency-root for its phase.

| ADR | Decides |
|-----|---------|
| [`adr/phase-2-eval-spine.md`](./adr/phase-2-eval-spine.md) | Eval spine: gold-set schema, pgvector KB store, Inspect AI task interface, threshold→CI-gate wiring (per-commit vs per-promotion), self-consistency confidence, Langfuse-canonical observability vs the authoritative `eval_metric` gate |

## Engineering docs

| Document | Contains |
|----------|----------|
| [`ci.md`](./ci.md) | CI pipeline: gates, jobs, how the build runs green |
