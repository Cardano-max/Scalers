# Skill registry entry — where-your-customer-lives

Updates the existing HELD row in `docs/skills/registry.md` (sec-owned, `1mk.1`)
with strip-complete + our-format path. **No green row → no agent use.** HELD by
design.

| Field | Value |
|---|---|
| **Skill** | `where-your-customer-lives` |
| **Bead** | `CustomerAcq-1mk.4` |
| **Upstream source** | "where-your-customer-lives" (r/ClaudeAI 20-skills list; family: `coreyhaines31/marketingskills`, MIT) |
| **Pinned commit** | `<PIN-AT-ADOPTION>` — sec resolves/verifies real 40-hex SHA at fetch |
| **Skill type** | Pattern-only re-authoring; prompt-only after strip |
| **What was stripped/sandboxed** | `fetch.py` (TLS disabled, `CERT_NONE`) **stripped**; `.env.example` + `GITHUB_TOKEN` read **stripped**; 67 parent-repo CLIs **not vendored**. Network via `engine/research/` adapter, TLS restored. |
| **Re-authored to our format** | Yes — `skills/where-your-customer-lives/` |
| **Our-format path** | `skills/where-your-customer-lives/` + `engine/research/` |
| **sec sign-off (security)** | **SUBMITTED** — strip complete; pending sec S1 |
| **Eval-gate status** | **PENDING-on-gold-set** |
| **Eligibility (gate green?)** | **NO** |
| **Adoption (operator-approved + assigned)** | **NONE** — not IN USE |
| **Which agent uses it** | (none yet) → intended: research engine, `find_communities` intent |
