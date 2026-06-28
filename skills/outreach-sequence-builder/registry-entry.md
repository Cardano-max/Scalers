# Skill registry entry — outreach-sequence-builder

NEW row to add to `docs/skills/registry.md` (sec-owned, `1mk.1`). **No green row →
no agent use.** HELD by design; additionally **release-gated by bead 439** (no real
sends until rvy.7/.8 pass).

| Field | Value |
|---|---|
| **Skill** | `outreach-sequence-builder` |
| **Bead** | `CustomerAcq-1mk.7` |
| **Upstream source** | "outreach-sequence-builder" (r/ClaudeAI 20-skills list) — mine-patterns-only |
| **Pinned commit** | `ORIGINAL (no upstream code vendored)` — sec resolves/verifies real 40-hex SHA at fetch |
| **Skill type** | Pattern-only re-authoring; prompt-only (enforcement = `engine/outreach/`) |
| **What was stripped/sandboxed** | Nothing vendored. The money/send capability of the REJECTED family (`coldoutboundskills` real-money; marketingskills email-SEND CLIs) is **not taken**. Sending is the harness side-effect boundary, **439-gated**; `cold-email-verifier` adapted as a deterministic verifier (broker-enrichment stripped). |
| **Re-authored to our format** | Yes — `skills/outreach-sequence-builder/` + `engine/outreach/` |
| **Our-format path** | `skills/outreach-sequence-builder/` + `engine/outreach/` |
| **sec sign-off (security)** | **SUBMITTED** — pending sec S1 |
| **Eval-gate status** | **PENDING-on-gold-set** (`evals/gold/outreach-smoke.jsonl`; calibration = rvy.7/.8) |
| **Eligibility (gate green?)** | **NO** — sec S1 + eval-gate + real SHA pending |
| **Adoption (operator-approved + assigned)** | **NONE** — not IN USE; **release-gated by 439** |
| **Which agent uses it** | (none yet) → intended: outreach engine (sequence planning) |
