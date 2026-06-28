# Skill Registry (source of truth for what may be used)

**Owner:** sec · **Gate:** [`vetting-protocol.md`](./vetting-protocol.md) · **Rule:** **no row → no agent use.**

A skill is usable by an agent **only** when it has a row here with: a pinned upstream commit, a green **sec sign-off**, a complete **stripped/sandboxed** list, a re-authored **our-format path**, a **PASS** eval-gate, **and** operator adopt-approval. Eligibility (gate green) and adoption (operator-approved + agent assigned) are separate — a skill that is eligible but not yet operator-approved is **not** in use.

This document is **governance + worked examples**, not the adopt-list. The operator approves the adopt-list separately; the rows below demonstrate the gate. Nothing here is `IN USE` yet.

## Status legend

| Status | Meaning |
|---|---|
| `REGISTERED — IN USE` | All gate steps green + operator-approved + assigned to an agent. Loadable. |
| `ELIGIBLE` | Steps 1–4 green; awaiting operator adopt-approval / agent assignment. **Not yet loadable.** |
| `HELD` | In the gate; one or more steps incomplete or PENDING. Not loadable. |
| `REJECTED` | Failed the gate (unstrippable money/destructive capability, unreadable code, no provenance, etc.). Never loadable. Patterns may be mined manually. |

> **Provenance note:** the alias names below (`brand-alchemy`, `map-your-market`, …) come from our R&D and **do not always exist verbatim upstream**. A row is INCOMPLETE until its *Pinned commit* holds a real 40-hex SHA verified at fetch time. `<PIN-AT-ADOPTION>` marks a required, not-yet-filled provenance field — any such row is non-loadable by definition.

---

## Registry table

| Skill (our name) | Upstream source + pinned commit | sec sign-off | What was stripped / sandboxed | Our-format path | Eval-gate (rvy) | Agent | Status |
|---|---|---|---|---|---|---|---|
| brand-alchemy *(worked example)* | `<org>/brand-alchemy` @ `<PIN-AT-ADOPTION>` | **APPROVED-AS-STRIPPED** (sec, see §Demo) — conditional on eval-gate + operator approval | `domain_checker.py` **stripped in full** (TLS-disabled DNS/RDAP network). See itemized list in §Demo. | `engine/skills/brand-alchemy/` *(prompt-only after strip; to be authored at adoption)* | **PENDING** (gold-set not yet run) | *(intended: brand-voice / strategist)* | **HELD** — eval-gate PENDING + operator approval pending |
| map-your-market | `<org>/map-your-market` @ `<PIN-AT-ADOPTION>` | **HELD** — strip required before sign-off | `fetch.py` (TLS disabled, `CERT_NONE`) → **strip**; reads `GITHUB_TOKEN` + ships `.env.example` → **strip** credential read; re-route through Firecrawl/Meta Ad Library adapter with TLS restored | *(to be authored after strip)* | PENDING | *(intended: research)* | **HELD** |
| where-your-customer-lives | `<org>/where-your-customer-lives` @ `<PIN-AT-ADOPTION>` | **HELD** — same family as map-your-market | `fetch.py` (TLS disabled) + `GITHUB_TOKEN`/`.env` read → **strip**; route via vetted adapter | *(to be authored)* | PENDING | *(intended: research)* | **HELD** |
| ads / ad-creative (`google-ads.js`) | `coreyhaines31/marketingskills` (`ads`/`ad-creative`) @ `<PIN-AT-ADOPTION>` | **REJECTED** (sec) | `tools/clis/google-ads.js` claims **direct ad-account WRITE**. Money/destructive class → executor never vendored. | — | — | none | **REJECTED** — mine patterns only |
| coldoutboundskills | `growthenginenowoslawski/coldoutboundskills` @ `<PIN-AT-ADOPTION>` | **REJECTED** (sec) | `.ts` scripts **SPEND REAL MONEY** (Dynadot bulk domain purchase) + create live Instantly/Smartlead campaigns. Money/destructive class. | — | — | none | **REJECTED** — mine patterns only |
| coreyhaines31/marketingskills (67 Node CLIs) | `coreyhaines31/marketingskills` @ `<PIN-AT-ADOPTION>` | **REJECTED by default** (sec) | 67 bundled CLIs read env API tokens, hit data brokers (apollo/zoominfo/clearbit/hunter) + **send real email** (resend/sendgrid/postmark). | — | — | none | **REJECTED** — opt-in only later, scoped creds + `--dry-run`, never default |
| Nuwa Skill Distiller | skillhub.club / lobehub (off-GitHub) | **REJECTED** (sec) | Unclear/no OSS license; auto-generates executable `SKILL.md` (generation + injection surface). | — | — | none | **REJECTED** — use first-party skill-creator |

---

## §Demo — brand-alchemy taken through all 4 steps (worked example, sec-signed)

This demonstrates the gate end-to-end on one skill, with the stripped-items list and sec sign-off. It does **not** put the skill in use — eval-gate is PENDING and operator adopt-approval is separate.

```
Skill:              brand-alchemy  (upstream: <org>/brand-alchemy)
Pinned commit:      <PIN-AT-ADOPTION>            # REQUIRED — alias name is not provenance; row non-loadable until filled
Reviewed by:        sec        Date: 2026-06-28
```

**Step 1 — READ.** `SKILL.md` + all shipped scripts read in full; no obfuscation/minification (would have been an auto-REJECT). Capability inventory:

| Source | Capability | Class |
|---|---|---|
| `domain_checker.py` — DNS/RDAP lookups | outbound network | Network |
| `domain_checker.py` — `ssl._create_unverified_context()` / `CERT_NONE` | **TLS verification disabled** | Network — TLS disabled (high severity) |
| `SKILL.md` (brand/positioning prompt) | pure prompt, no I/O | Prompt/deterministic |

**Step 2 — STRIP (itemized).**

- `domain_checker.py` — **stripped in full.** Reason: TLS-disabled outbound DNS/RDAP is an unintended network capability (silent MITM/exfil surface) we did not ask for; the brand-voice value lives entirely in the prompt, not the script. Action: **remove the script; do not vendor it.** If domain/availability checks are ever wanted, re-introduce via our vetted adapter with TLS verification restored — never this code.
- Result: surviving surface = `SKILL.md` (+ any `references/*.md`) only. Prompt-only.

**Step 3 — RE-AUTHOR + PIN.** Re-author the prompt into our Agent Skills format under `engine/skills/brand-alchemy/`, applying brand-voice + determinism rules (temp-0, pinned models per HARN-06). Pin the verified upstream commit SHA (REQUIRED before any use).

**Step 4 — EVAL-GATE.** Gold-set = brand-voice gold-set (Phase-2 `rvy`). Result = **PENDING** (eval spine not yet wired/run). Until PASS, status stays HELD.

```
SEC VERDICT:    APPROVED-AS-STRIPPED — prompt-only residue is safe to proceed to eval-gate.
                Becomes ELIGIBLE only on (a) real pinned SHA, (b) eval-gate PASS.
                Becomes IN USE only on operator adopt-approval + agent assignment.
Residual risk (for arch/operator): LOW after strip — no network/file/exec/credential surface
                remains. Pre-strip max severity: HIGH (TLS-disabled outbound).
```

---

## How an agent uses this registry

1. Want to use skill X? Find its row. If there is no row, or status ≠ `REGISTERED — IN USE`, **do not use it** (and do not run any bundled script).
2. Load only the artifact at *Our-format path*, which is pinned to the re-vetted upstream commit.
3. On any upstream version bump, the row is invalidated until sec re-runs Steps 1–4 (re-vet on drift).
