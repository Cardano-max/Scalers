# Skill registry entry — cold-email-verifier

NEW row to add to `docs/skills/registry.md` (sec-owned, `1mk.1`). **No green row →
no agent use.** HELD by design.

| Field | Value |
|---|---|
| **Skill** | `cold-email-verifier` (verifier-only) |
| **Bead** | `CustomerAcq-1mk.7` |
| **Upstream source** | "cold-email-verifier" (r/ClaudeAI 20-skills list) — verify-half only |
| **Pinned commit** | `ORIGINAL (no upstream code vendored)` — sec resolves/verifies real 40-hex SHA at fetch |
| **Skill type** | Pattern-only re-authoring; deterministic in-house validator |
| **What was stripped/sandboxed** | **guess + enrich + autonomous-CSV** (data-broker enrichment, apollo/hunter class — REJECTED) **not adopted**; **no send**; deterministic verify-only (syntax/disposable/role/shape). Live MX probe is a separate eng seam (own resolver + TLS). No network in the core. |
| **Re-authored to our format** | Yes — `skills/cold-email-verifier/` + `engine/outreach/verifier.py` |
| **Our-format path** | `skills/cold-email-verifier/` + `engine/outreach/verifier.py` |
| **sec sign-off (security)** | **SUBMITTED** — pending sec S1 |
| **Eval-gate status** | **PENDING-on-gold-set** (`evals/gold/outreach-smoke.jsonl`) |
| **Eligibility (gate green?)** | **NO** — sec S1 + eval-gate + real SHA pending |
| **Adoption (operator-approved + assigned)** | **NONE** — not IN USE |
| **Which agent uses it** | (none yet) → intended: outreach engine (deliverability gate) |
