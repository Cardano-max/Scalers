# Vetting record — map-your-market (CustomerAcq-1mk.4)

Growth-side vetting/registration record for the 1mk.1 supply-chain gate. **sec
owns the S1 sign-off** (status below is SUBMITTED, pending sec). Canonical
registry row: `docs/skills/registry.md` (sec). **ELIGIBLE ≠ IN USE** — no agent
loads this skill until sec signs off, the eval-gate passes, and the operator
approves adoption + agent assignment.

| Field | Value |
|-------|-------|
| Skill | `map-your-market` |
| Upstream pattern | "map-your-market" (r/ClaudeAI 20-skills list; family: coreyhaines31/marketingskills, MIT) |
| Pinned commit | `8bfcdffb655f16e713940cd04fb08891899c47db` — sec to resolve/verify the real 40-hex SHA at fetch; non-loadable until filled |
| Skill type | Pattern-only re-authoring; prompt-only after strip |
| Our-format path | `skills/map-your-market/` |
| Enforcement / wiring | `engine/research/` adapter + router (network seam; live providers eng-owned) |
| sec sign-off (S1) | **SUBMITTED — strip complete, pending sec verification** |
| Eval-gate status | **PENDING-on-gold-set** (`evals/gold/research-niche-smoke.jsonl` smoke; holdout = Phase-2 `rvy`) |
| Status | **HELD** — pending sec S1 + eval-gate + operator adopt-approval |

## 4-step gate

1. **READ** — upstream `SKILL.md` + every shipped script read. Capability
   inventory: `fetch.py` does outbound network with **TLS disabled**
   (`ssl._create_unverified_context()` / `CERT_NONE`) and reads `GITHUB_TOKEN` /
   ships `.env.example`. The parent `coreyhaines31/marketingskills` bundles 67
   live-API CLIs (data-brokers + email senders).
2. **STRIP** — `fetch.py` **stripped in full** (TLS-disabled network is an
   unintended capability; the value is the prompt, not the script). `.env.example`
   + `GITHUB_TOKEN` read **stripped** (no credential harvesting). The 67 CLIs are
   **not vendored**. Surviving surface: `SKILL.md` + `references/*.md` only. All
   network re-routed through the vetted research adapter with **TLS restored**.
3. **RE-AUTHOR + PIN** — re-authored as original methodology (pattern-only;
   reproduces no upstream text), retargeted to tattoo-native sources, grounded in
   `docs/skills/winning-strategies-kb.md`. Determinism: the skill only retrieves +
   structures; any ranking/summarizing runs in a temp-0 cell downstream. **Pin
   required** — sec fills the verified SHA at fetch.
4. **EVAL-GATE** — smoke set `evals/gold/research-niche-smoke.jsonl`; the research
   relevance/niche-fit gate runs against the Phase-2 `rvy` holdout. **PENDING**
   until that lands (not a blocker for SUBMITTED, per the brand-voice precedent).

## What was stripped

`fetch.py` (TLS-disabled outbound network) — removed in full. `.env.example` +
`GITHUB_TOKEN` credential read — removed. 67 parent-repo live-API CLIs — not
vendored. Residual surface after strip: **none** (prompt-only). Pre-strip max
severity: **HIGH** (TLS-disabled outbound + credential read).
