# Skill registry entry — map-your-market

Row to merge/update in `docs/skills/registry.md` (sec-owned, `1mk.1`). The HELD
row already exists; this updates it with strip-complete + our-format path. Per the
HARD RULE, **no green row → no agent use.** Ships eligibility **HELD** by design.

| Field | Value |
|---|---|
| **Skill** | `map-your-market` |
| **Bead** | `CustomerAcq-1mk.4` |
| **Upstream source** | "map-your-market" (r/ClaudeAI 20-skills list; family: `coreyhaines31/marketingskills`, MIT) |
| **Pinned commit** | `8bfcdffb655f16e713940cd04fb08891899c47db` — sec resolves/verifies the real 40-hex SHA at fetch; non-loadable until filled |
| **Skill type** | Pattern-only re-authoring; prompt-only after strip |
| **What was stripped/sandboxed** | `fetch.py` (TLS disabled, `CERT_NONE`) **stripped in full**; `.env.example` + `GITHUB_TOKEN` read **stripped**; 67 parent-repo live-API CLIs **not vendored**. Network re-routed through `engine/research/` adapter with TLS restored. |
| **Re-authored to our format** | Yes — `skills/map-your-market/` (original methodology, tattoo-retargeted, grounded in winning-strategies-kb.md) |
| **Our-format path** | `skills/map-your-market/` + `engine/research/` (adapter seam) |
| **sec sign-off (security)** | **SUBMITTED** — strip complete; pending sec S1 verification |
| **Eval-gate status** | **PENDING-on-gold-set** (`evals/gold/research-niche-smoke.jsonl`; holdout = Phase-2 `rvy`) |
| **Eligibility (gate green?)** | **NO** — pending sec S1 + eval-gate + real pinned SHA |
| **Adoption (operator-approved + assigned)** | **NONE** — not IN USE |
| **Which agent uses it** | (none yet) → intended: research engine, `map_market` intent |
