# State: Scalers

## Project Reference

See: `.planning/PROJECT.md` (updated 2026-06-28)

**Core value:** Reliable, auditable marketing autonomy — never fires off-brand/unsafe content without operator sign-off.
**Current focus:** Phase 1 — Foundations & Control core

## Position

- **Milestone:** v1 (engine-first, single client)
- **Phase:** 1 of 8 — not yet started (planning complete)
- **Mode:** yolo / coarse / parallel · per-phase research off (front-loaded) · plan-check + verifier on
- **Repo:** github.com/Cardano-max/Scalers (bootstrapped; write access confirmed as Cardano-max)
- **Tracking:** GSD roadmap (this `.planning/`) → beads (bd) → initech worker fleet

## Phase Status

| # | Phase | Status |
|---|-------|--------|
| 1 | Foundations & Control core | Pending (next) |
| 2 | Eval spine & gold set | Pending |
| 3 | First vertical slice (posting) | Pending |
| 4 | Console + API wiring | Pending |
| 5 | Autonomy engine | Pending |
| 6 | Real tooling & deliverability | Pending |
| 7 | Remaining engines, research & knowledge | Pending |
| 8 | Harden & scale | Pending |

## Key Facts / Decisions

- No agent framework owns control flow; hand-coded deterministic Python graph (harness law).
- Durable substrate: DBOS Transact (v1) — Postgres-only, fast ship; exactly-once enforced at DB boundary; swappable to Restate later.
- Confidence via self-consistency variance (hosted Claude exposes no logprobs).
- Gold set must exist before scaling (Phase 2 gate, per senior-ML critique).
- Frontend locked & generic (Operator Console); niche in backend per-tenant config.
- Stack vetted for June 28 2026; authoritative detail in `super/scalers-backend-plan.md` (orchestration repo) + `.planning/research/SUMMARY.md`.

## Open Questions (operator)

1. Outreach audience + sending domain/inbox + expected list size (for MAIL config).
2. First-wave engine for the vertical slice — posting (default) vs outreach.
3. `<provided>` config numbers — being researched (config workflow); operator to confirm/override.

## Next Action

Groom beads from Phase 1 + Phase 2 requirements (proper AC), provision agent workspaces (`initech init`), dispatch in parallel to eng1/eng2/eng3 + arch + qa.

---
*Last updated: 2026-06-28 after initialization*
