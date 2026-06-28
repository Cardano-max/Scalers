# Vetting record — competitor-pr-finder (CustomerAcq-1mk.4)

Growth-side record for the 1mk.1 gate. **sec owns the S1 sign-off** (SUBMITTED,
pending sec). Canonical row: `docs/skills/registry.md` (new row — not previously
in the registry). **ELIGIBLE ≠ IN USE.**

| Field | Value |
|-------|-------|
| Skill | `competitor-pr-finder` |
| Upstream pattern | "competitor-pr-finder" (r/ClaudeAI 20-skills list; family: coreyhaines31/marketingskills, MIT) |
| Pinned commit | `8bfcdffb655f16e713940cd04fb08891899c47db` — sec resolves/verifies real 40-hex SHA at fetch |
| Skill type | Pattern-only re-authoring; prompt-only after strip |
| Our-format path | `skills/competitor-pr-finder/` |
| Enforcement / wiring | `engine/research/` adapter (Meta-Ad-Library/Foreplay provider; eng-owned live client) |
| sec sign-off (S1) | **SUBMITTED — strip complete, pending sec verification** |
| Eval-gate status | **PENDING-on-gold-set** (`evals/gold/research-niche-smoke.jsonl`; holdout = Phase-2 `rvy`) |
| Status | **HELD** — pending sec S1 + eval-gate + operator adopt-approval |

## 4-step gate

1. **READ** — `SKILL.md` + scripts read. Family ships TLS-disabled `fetch.py` +
   `GITHUB_TOKEN`/`.env` reads; parent repo bundles 67 live-API CLIs.
2. **STRIP** — bundled network/fetch scripts **not vendored**; 67 CLIs **not
   vendored**. Competitor-ad access re-routed through the vetted
   Meta-Ad-Library/Foreplay adapter (official API, TLS on). Surviving surface:
   `SKILL.md` + `references/*.md` only.
3. **RE-AUTHOR + PIN** — original methodology, retargeted from press/journalist
   mining to tattoo ad-creative mining, grounded in winning-strategies-kb.md. IP:
   extracts angles/patterns, never reproduces competitor creative. Pin required.
4. **EVAL-GATE** — smoke set + Phase-2 `rvy` holdout. **PENDING**. The
   false-positive/match-confidence behavior is covered by the adapter tests
   (`test_research_adapter.py`).

## What was stripped

Bundled TLS-disabled fetch script(s) + `GITHUB_TOKEN`/`.env` reads — not vendored.
67 parent-repo CLIs — not vendored. Residual after strip: **none** (prompt-only).
Pre-strip max severity: **HIGH**.
