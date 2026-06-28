# Scalers Roadmap

Strategic sequencing: milestones, phases, gates, agent allocation. Beads handle the tactical layer. Mirrors the Scalers repo `.planning/ROADMAP.md` (canonical phase plan). Owner: super.

---

## 1. Phases

### Phase 0: Project documents (in progress)
**Goal:** All four project docs written + aligned to `docs/stack-decision.md`.
**Work:** pm → prd.md · arch → systemdesign.md · super → spec.md + roadmap.md.
**Gate:** operator reviews all four; team can answer what/why/how/when.
**Beads:** epic `CustomerAcq-8jc` (.1 prd, .2 systemdesign, .3 spec, .4 roadmap).

### Phase 1: Foundations & Control core (in progress)
**Goal:** deterministic harness runs a typed cell end-to-end with durable, exactly-once execution.
**Packages:** infra (docker-compose), engine (LangGraph skeleton + Postgres checkpointer + router), typed-cell framework (Pydantic-AI), Postgres status store, exactly-once boundary, per-tenant config, CI/eval scaffold. Meta app review submitted day one.
**Gate:** `docker compose up` healthy; demo graph runs deterministically; forced-crash resumes once; router tested; CI green.
**Beads:** epic `CustomerAcq-dhv` (.1 ADR, .2 docker, .3 skeleton, .4 cells, .5 CI, .6 status store, .7 exactly-once, .8 per-tenant config, .9 Meta review).

### Phase 2: Eval spine & gold set
**Goal:** eval-on-every-change against a real gold set with calibration gates. **Gate:** gold set (30–200/engine) exists + Inspect AI CI gate + ECE/F1 thresholds.

### Phase 3: First vertical slice (posting, mock tooling)
**Goal:** one engine produces a real validated post that lands in the review queue.

### Phase 4: Console + API wiring
**Goal:** the locked Operator Console runs on live GraphQL + SSE (mocks replaced). **UI phase.**

### Phase 5: Autonomy engine
**Goal:** jury + calibrated confidence + gates + safety route auto vs review.

### Phase 6: Real tooling & deliverability
**Goal:** real IG/FB publishing + Gmail deliverability via exactly-once side effects (Meta review must be approved by now).

### Phase 7: Remaining engines, research & knowledge
**Goal:** outreach + engagement engines + shared research + feedback/memory live.

### Phase 8: Harden & scale
**Goal:** reliability scorecard + OWASP-agentic red-team + latency/cost budgets met.

## 2. Milestone Summary

**v1 (engine-first, single client):** Phases 0–8. Ship a hands-off-except-approvals engine that drives measurable, on-brand marketing for one tattoo studio across IG/FB posting, Gmail outreach, and comment/DM engagement, operated from the generic Console. Success = the measurable targets in `docs/spec.md` §5 met on a real gold set.

## 3. Agent Allocation

| Agent | Domain |
|-------|--------|
| pm | PRD, requirements grooming |
| arch | system design, interfaces, ADRs |
| eng1/eng2/eng3 | engine, gateway, infra, tooling (parallel by package) |
| qa1/qa2 | eval suite, CI gates, test, verification |
| ops | infra/MCP servers, Meta app review, deliverability ops |
| shipper | PRs, releases, merges |
| sec | secrets, OWASP-agentic red-team, platform-policy compliance |
| pmm | positioning of the generic console / packs (later) |
| writer | brand-voice skill material, docs (later) |
| growth | research-layer tuning, winning-angle analysis (later) |
| super | coordination, doc alignment, bead lifecycle, QA routing |

## 4. Risk Gates

1. **Gold set before scaling** (Phase 2) — no auto-routing trusted until calibrated on real ground truth.
2. **Meta app review** submitted day one (Phase 1); must be approved before Phase 6 real publishing.
3. **Exactly-once** proven (Phase 1) before any real send/publish.
4. **Latency/cost budgets** set before scaling engine 3 (realtime).
5. **Branch protection + PR review** on Scalers `main` before heavy parallel eng pushes.
6. **Graph API** v25 pinned; migrate v26 (Sep 2026) + reach→Page Viewer Metric (June 2026) before they break evals.

---
*Owner: super · Last updated: 2026-06-28 · mirrors Scalers .planning/ROADMAP.md*
