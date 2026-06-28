# Skill registry entry — competitor-pr-finder

NEW row to add to `docs/skills/registry.md` (sec-owned, `1mk.1`) — this skill is
not yet in the registry. **No green row → no agent use.** HELD by design.

| Field | Value |
|---|---|
| **Skill** | `competitor-pr-finder` |
| **Bead** | `CustomerAcq-1mk.4` |
| **Upstream source** | "competitor-pr-finder" (r/ClaudeAI 20-skills list; family: `coreyhaines31/marketingskills`, MIT) |
| **Pinned commit** | `<PIN-AT-ADOPTION>` — sec resolves/verifies real 40-hex SHA at fetch |
| **Skill type** | Pattern-only re-authoring; prompt-only after strip |
| **What was stripped/sandboxed** | Bundled TLS-disabled fetch script(s) + `GITHUB_TOKEN`/`.env` reads **not vendored**; 67 parent-repo CLIs **not vendored**. Competitor-ad access via `engine/research/` Meta-Ad-Library/Foreplay adapter (official API, TLS on). |
| **Re-authored to our format** | Yes — `skills/competitor-pr-finder/` |
| **Our-format path** | `skills/competitor-pr-finder/` + `engine/research/` |
| **sec sign-off (security)** | **SUBMITTED** — strip complete; pending sec S1 |
| **Eval-gate status** | **PENDING-on-gold-set** |
| **Eligibility (gate green?)** | **NO** |
| **Adoption (operator-approved + assigned)** | **NONE** — not IN USE |
| **Which agent uses it** | (none yet) → intended: research engine, `competitor_creatives` intent |
