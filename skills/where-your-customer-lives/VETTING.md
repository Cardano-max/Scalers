# Vetting record — where-your-customer-lives (CustomerAcq-1mk.4)

Growth-side record for the 1mk.1 gate. **sec owns the S1 sign-off** (SUBMITTED,
pending sec). Canonical row: `docs/skills/registry.md`. **ELIGIBLE ≠ IN USE.**

| Field | Value |
|-------|-------|
| Skill | `where-your-customer-lives` |
| Upstream pattern | "where-your-customer-lives" (r/ClaudeAI 20-skills list; family: coreyhaines31/marketingskills, MIT) |
| Pinned commit | `<PIN-AT-ADOPTION>` — sec resolves/verifies real 40-hex SHA at fetch |
| Skill type | Pattern-only re-authoring; prompt-only after strip |
| Our-format path | `skills/where-your-customer-lives/` |
| Enforcement / wiring | `engine/research/` adapter + router (network seam; live providers eng-owned) |
| sec sign-off (S1) | **SUBMITTED — strip complete, pending sec verification** |
| Eval-gate status | **PENDING-on-gold-set** (`evals/gold/research-niche-smoke.jsonl`; holdout = Phase-2 `rvy`) |
| Status | **HELD** — pending sec S1 + eval-gate + operator adopt-approval |

## 4-step gate

1. **READ** — `SKILL.md` + scripts read. Same family as `map-your-market`:
   `fetch.py` does outbound network with **TLS disabled** and reads `GITHUB_TOKEN`
   / ships `.env.example`; parent repo bundles 67 live-API CLIs.
2. **STRIP** — `fetch.py` **stripped in full**; `.env.example` + `GITHUB_TOKEN`
   read **stripped**; 67 CLIs **not vendored**. Network re-routed through the
   vetted adapter with TLS restored. Surviving surface: `SKILL.md` +
   `references/*.md` only.
3. **RE-AUTHOR + PIN** — original methodology, tattoo-retargeted, grounded in
   winning-strategies-kb.md. Pin required (sec fills SHA at fetch).
4. **EVAL-GATE** — smoke set + Phase-2 `rvy` holdout. **PENDING**.

## What was stripped

`fetch.py` (TLS-disabled outbound network), `.env.example` + `GITHUB_TOKEN` read,
67 parent-repo CLIs. Residual after strip: **none** (prompt-only). Pre-strip max
severity: **HIGH**.
