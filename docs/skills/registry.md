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
| **human-tone** *(1mk.3, first real sign-off)* | `Varnan-Tech/opendirectory` — "human-tone" @ `9c30f79eb975c50a97bed10b47e14f18116a3e3b` (MIT; `main` HEAD at vetting 2026-06-28) | **APPROVED — ELIGIBLE** (sec, see §Sign-off) | **Nothing stripped** — upstream is pure markdown (no scripts/network/file/exec); read in full for prompt-injection/off-policy, none found. Enforcement re-authored as our own pure-code validator + temp-0 cell. | `engine/skills/human-tone/SKILL.md` + `engine/cells/ai_flagger.py` (validator) + `engine/cells/humanize.py` (temp-0 rewrite) — PR #26 | **PASS** — `test_ai_flagger.py::test_labeled_set_separates_slop_from_human` (recall 1.0, FP 0.0); 173 unit pass, ruff clean | *(eligible for: AI-flagger validator / voice-QA; assigned at adoption)* | **ELIGIBLE** — gate green; **operator adopt-approval pending** (not yet IN USE) |
| **brand-voice** *(1mk.2, per-artist)* | `anthropics/skills` — `skills/brand-guidelines` @ `b9e19e6f44773509fbdd7001d77ff41a49a486c1` (Apache-2.0; 2026-04-20) | **APPROVED — ELIGIBLE (conditional)** (sec, see §Sign-off) | **Nothing stripped** — upstream is prompt-only markdown; **structure-only** derivative, none of Anthropic's brand content (colors/type/marks) reproduced; NOTICE satisfies Apache-2.0 §4, trademarks excluded. Shipped `verify/*.py` are **our own** stdlib-only resolver+demo (no net/exec/env; read-only repo config + temp writes). | `skills/brand-voice/` (SKILL.md + per-tenant DNA + examples) — PR #29 | **PENDING-on-gold-set** — brand-voice ≥90% gate runs once the eval gold set (rvy.10 smoke) exists; **not a blocker for ELIGIBLE** (operator-authorized) | *(intended: posting/reply/outreach writing cells; assigned at adoption)* | **ELIGIBLE (conditional)** — steps 1–3 green; eval-gate deferred-by-dependency; operator adopt-approval pending; **must clear ≥90% gate before production writing use** |
| brand-alchemy *(worked example)* | `<org>/brand-alchemy` @ `<PIN-AT-ADOPTION>` | **APPROVED-AS-STRIPPED** (sec, see §Demo) — conditional on eval-gate + operator approval | `domain_checker.py` **stripped in full** (TLS-disabled DNS/RDAP network). See itemized list in §Demo. | `engine/skills/brand-alchemy/` *(prompt-only after strip; to be authored at adoption)* | **PENDING** (gold-set not yet run) | *(intended: brand-voice / strategist)* | **HELD** — eval-gate PENDING + operator approval pending |
| map-your-market *(1mk.4)* | "map-your-market" (r/ClaudeAI 20-skills; family `coreyhaines31/marketingskills`, MIT) @ `8bfcdffb655f16e713940cd04fb08891899c47db` (**ORIGINAL / pattern-only** — alias NOT verbatim upstream; reviewed family-ref commit, nothing copied) | **APPROVED — ELIGIBLE** (sec S1 2026-06-28, see §Sign-off) | `fetch.py` (TLS disabled, `CERT_NONE`) **stripped in full**; `GITHUB_TOKEN` + `.env.example` credential read **stripped**; 67 parent-repo live-API CLIs **not vendored**. Network re-routed through `engine/research/` adapter (Firecrawl/Meta-Ad-Library, TLS restored). See `skills/map-your-market/VETTING.md`. | `skills/map-your-market/` + `engine/research/` (pattern-only, prompt-only after strip) | **PENDING-on-gold-set** (`evals/gold/research-niche-smoke.jsonl`; holdout = `rvy`) | *(intended: research — `map_market`)* | **ELIGIBLE (conditional)** — strip verified by sec (zero live network; prompt-only; allowlist seam); eval-gate PENDING-on-gold-set; operator adoption pending; **live Firecrawl/Meta provider impl (currently stubs) MUST be re-vetted by sec before going live** |
| where-your-customer-lives *(1mk.4)* | "where-your-customer-lives" (r/ClaudeAI 20-skills; family `coreyhaines31/marketingskills`, MIT) @ `8bfcdffb655f16e713940cd04fb08891899c47db` (**ORIGINAL / pattern-only** — alias NOT verbatim upstream; reviewed family-ref commit, nothing copied) | **APPROVED — ELIGIBLE** (sec S1 2026-06-28, see §Sign-off) | `fetch.py` (TLS disabled) **stripped**; `GITHUB_TOKEN`/`.env` read **stripped**; 67 parent-repo CLIs **not vendored**. Network via `engine/research/` adapter, TLS restored. See `skills/where-your-customer-lives/VETTING.md`. | `skills/where-your-customer-lives/` + `engine/research/` | **PENDING-on-gold-set** | *(intended: research — `find_communities`)* | **ELIGIBLE (conditional)** — strip verified by sec (zero live network; prompt-only; allowlist seam); eval-gate PENDING-on-gold-set; operator adoption pending; **live Firecrawl/Meta provider impl (currently stubs) MUST be re-vetted by sec before going live** |
| competitor-pr-finder *(1mk.4)* | "competitor-pr-finder" (r/ClaudeAI 20-skills; family `coreyhaines31/marketingskills`, MIT) @ `8bfcdffb655f16e713940cd04fb08891899c47db` (**ORIGINAL / pattern-only** — alias NOT verbatim upstream; reviewed family-ref commit, nothing copied) | **APPROVED — ELIGIBLE** (sec S1 2026-06-28, see §Sign-off) | Bundled TLS-disabled fetch script(s) + `GITHUB_TOKEN`/`.env` reads **not vendored**; 67 parent-repo CLIs **not vendored**. Competitor-ad access via `engine/research/` Meta-Ad-Library/Foreplay adapter (official API, TLS on). See `skills/competitor-pr-finder/VETTING.md`. | `skills/competitor-pr-finder/` + `engine/research/` | **PENDING-on-gold-set** | *(intended: research — `competitor_creatives`)* | **ELIGIBLE (conditional)** — strip verified by sec (zero live network; prompt-only; allowlist seam); eval-gate PENDING-on-gold-set; operator adoption pending; **live Firecrawl/Meta provider impl (currently stubs) MUST be re-vetted by sec before going live** |
| ads / ad-creative (`google-ads.js`) | `coreyhaines31/marketingskills` (`ads`/`ad-creative`) @ `<PIN-AT-ADOPTION>` | **REJECTED** (sec) | `tools/clis/google-ads.js` claims **direct ad-account WRITE**. Money/destructive class → executor never vendored. | — | — | none | **REJECTED** — mine patterns only |
| coldoutboundskills | `growthenginenowoslawski/coldoutboundskills` @ `<PIN-AT-ADOPTION>` | **REJECTED** (sec) | `.ts` scripts **SPEND REAL MONEY** (Dynadot bulk domain purchase) + create live Instantly/Smartlead campaigns. Money/destructive class. | — | — | none | **REJECTED** — mine patterns only |
| coreyhaines31/marketingskills (67 Node CLIs) | `coreyhaines31/marketingskills` @ `<PIN-AT-ADOPTION>` | **REJECTED by default** (sec) | 67 bundled CLIs read env API tokens, hit data brokers (apollo/zoominfo/clearbit/hunter) + **send real email** (resend/sendgrid/postmark). | — | — | none | **REJECTED** — opt-in only later, scoped creds + `--dry-run`, never default |
| Nuwa Skill Distiller | skillhub.club / lobehub (off-GitHub) | **REJECTED** (sec) | Unclear/no OSS license; auto-generates executable `SKILL.md` (generation + injection surface). | — | — | none | **REJECTED** — use first-party skill-creator |

---

## §Sign-off — research skills (1mk.4: map-your-market, where-your-customer-lives, competitor-pr-finder)

Heavy vet (operator-flagged TLS-disabled-fetch concern). sec verified PR #37 independently.

```
Skills:       map-your-market, where-your-customer-lives, competitor-pr-finder
Provenance:   ORIGINAL / pattern-only — reproduce NO upstream text. The alias names do
              NOT exist verbatim upstream (verified: coreyhaines31/marketingskills @
              8bfcdff has "competitor-profiling", not these three). Pinned the reviewed
              family-ref commit; nothing was copied.
Pinned:       coreyhaines31/marketingskills @ 8bfcdffb655f16e713940cd04fb08891899c47db (MIT)
Reviewed by:  sec    Date: 2026-06-28
```

**Step 1 READ + Step 2 STRIP (verified, not on report).** Across the whole PR: **no `fetch.py`**; **no TLS-disable in code** (every `ssl._create_unverified_context`/`CERT_NONE` hit is documentation of the strip); **no `GITHUB_TOKEN`/`.env`/`os.environ` harvesting code**; **no `.js`/`.ts`/67 CLIs**; **no `subprocess`/`exec`/`eval`**. The 3 skills are prompt-only methodology; references encode safe-fetch guardrails.

**Network seam.** All network is centralized in `engine/research/` behind a `SourceProvider` protocol. Live providers (`FirecrawlProvider`, `MetaAdLibraryProvider`) are **contract-only stubs that raise `NotImplementedError`** → **zero live network today**. `Document.tls_verified=True`; keys are constructor-injected from the tenant pack secret (never `.env`/`GITHUB_TOKEN`). `ResearchRouter` does no network and enforces a **vetted-provider allowlist** (a pack cannot conjure an un-vetted provider). `FixtureProvider` is offline/deterministic.

**Step 4 EVAL-GATE.** PENDING-on-gold-set (`evals/gold/research-niche-smoke.jsonl` smoke + `rvy` holdout).

```
SEC VERDICT:  APPROVED (strip/security) — ELIGIBLE (CONDITIONAL). Steps 1–3 green;
              prompt-only; zero live network; allowlist seam. NOT "IN USE": operator
              adoption + agent assignment separate.
HARD RE-VET GATE (blocking before research can run): the live Firecrawl/Meta provider
              implementation is unwritten. Before eng wires it, sec MUST re-vet:
              (1) verified TLS actually used in code, (2) key from tenant pack secret only,
              (3) official-API-only (no scraping), (4) SSRF guard on provider.fetch(url)
              (arbitrary-URL fetch is an SSRF surface), (5) rate-limit/ToS.
Re-vet triggers: live-provider implementation; OR upstream family bump; OR eval-gate result.
```

---

## §Sign-off — brand-voice (1mk.2, sec sign-off; eval-gate deferred by dependency)

The per-artist brand-voice skill (writer, PR #29). sec applied the gate independently; eval-gate is the one step not green, and only because its gold set does not exist yet — this is an **operator-authorized conditional eligibility**, not a passed quality gate.

```
Skill:              brand-voice  (upstream: anthropics/skills — skills/brand-guidelines)
Pinned commit:      b9e19e6f44773509fbdd7001d77ff41a49a486c1   # verified via GitHub API; 2026-04-20
License:            Apache-2.0  (verified: skills/brand-guidelines/LICENSE.txt at the pinned commit)
Reviewed by:        sec        Date: 2026-06-28
```

**Step 1 — READ.** SKILL.md + README + NOTICE + brand-DNA template + ink-studio DNA + examples + both `verify/*.py` read in full. SKILL.md is instructions-only and defers to the validator bank / jury / confidence gate (it grounds voice, makes no decision). No prompt-injection / gate-subversion / off-policy markers in SKILL.md or the tenant DNA (the live prompt-prepended surface).

**Step 2 — STRIP / REDISTRIBUTION.**
- **Upstream nothing-to-strip:** confirmed — `anthropics/skills/skills/brand-guidelines` is prompt-only markdown; no network/file/exec/credential/money surface.
- **Redistribution terms (the licensing ask):** independently verified the pinned commit exists and `LICENSE.txt` at it is **Apache-2.0**. Apache-2.0 permits reproduction/modification/distribution of derivative works royalty-free; the bundle's `NOTICE` satisfies §4 (license reference, modification notice, attribution retained). **Trademarks correctly NOT used.** It is a **structure-only** derivative — none of Anthropic's brand content (colors `#141413`/`#d97757`, typography, pptx styling) is reproduced; all voice content is original, grounded in `docs/skills/winning-strategies-kb.md`. Redistribution is clean.
- **Shipped scripts (writer's own, not upstream):** `verify/resolve_brand_voice.py` (the runtime resolver contract) + `verify/demo_brand_grounding.py` (manual demo) are **stdlib-only** (`json`/`tomllib`/`re`/`sys`/`tempfile`/`pathlib`). No `socket`/`requests`/`urllib`/`subprocess`/`os.system`/`eval`/`exec`/`getenv`/`ssl`; no model call. File access is read-only on repo config; the demo writes only to a `TemporaryDirectory`. Nothing to strip.

**Step 3 — RE-AUTHOR + PIN.** Re-authored into our format (`skills/brand-voice/`) with determinism intent (grounding only; the cell's output still flows through every downstream gate). Pinned to `b9e19e6f…` (re-vet on any upstream bump).

**Step 4 — EVAL-GATE.** **PENDING-on-gold-set.** The brand-voice **≥90%** quality gate runs once the eval gold set (rvy.10 smoke / holdout) is built — it does not exist yet. Per operator direction this is **not a blocker for ELIGIBLE/registry**, but the skill **MUST clear the ≥90% gate before any production writing use**.

```
SEC VERDICT:    APPROVED — ELIGIBLE (CONDITIONAL). Steps 1–3 green; provenance +
                Apache-2.0 redistribution verified; secret/PII scan = 0 hits.
                Eval-gate deferred by dependency (gold set not built) — operator-
                authorized. NOT "IN USE": operator approves adoption + agent
                assignment separately, AND the ≥90% gate must pass first.
Residual (for arch/operator, non-blocking):
  - [RESOLVED — PR #29 @ cbd3b43, sec-verified] path-traversal hardening on
    tenant_id/skill_ref. resolve_brand_voice.py now allowlists each segment
    (^[A-Za-z0-9][A-Za-z0-9_-]*$) + a _within() containment check before any fs
    access. Verified: the verify demo rejects '../../etc/passwd', '..', 'a/b',
    '/abs', 'C:\win', and skill_ref '../../../../secrets' (BrandVoiceError), legit
    ids still resolve, demo exit 0. (Originally LOW — internal pack config only.)
  - Re-vet trigger: upstream commit bump OR the ≥90% eval-gate result (flip
    PENDING→PASS/FAIL when the gold set lands).
```

---

## §Sign-off — human-tone (1mk.3, real sec sign-off through all 4 steps)

The first real entry. eng3 re-authored the skill and recorded its 4-step record at `engine/skills/human-tone/VETTING.md` (PR #26); sec independently re-ran the gate below.

```
Skill:              human-tone  (upstream: Varnan-Tech/opendirectory — "human-tone")
Pinned commit:      9c30f79eb975c50a97bed10b47e14f18116a3e3b   # main HEAD, resolved via ls-remote at vetting
License:            MIT
Reviewed by:        sec        Date: 2026-06-28
```

**Step 1 — READ.** `SKILL.md` + both enforcement modules read in full. Upstream ships **pure markdown, no scripts**. Re-authored enforcement read line-by-line: `engine/cells/ai_flagger.py` imports only `re` / `dataclasses` / `enum` / `cells.validators`; `engine/cells/humanize.py` imports only `pydantic` / `cells.*`. **No** `socket`/`requests`/`urllib`/`httpx`/`subprocess`/`os`/`sys`/`open`/`Path`/`eval`/`exec`/`pickle`/`getenv`/`environ`/`ssl` in either. No obfuscation. SKILL.md read for prompt-injection / off-policy — none found.

**Step 2 — STRIP.** **Nothing to strip** — confirmed independently, not taken on report. No network/file/exec/credential surface in upstream (markdown-only) or in the re-authored code. The detector is pure regex with no I/O; the only model call is the temp-0 `humanize` rewrite cell, which is gated behind the deterministic flagger and re-checks its own output (AI-flagger ERROR validator + `claims_preserved` ERROR validator, so it cannot silently add/drop claims).

**Step 3 — RE-AUTHOR + PIN.** Re-authored into our Agent Skills format with determinism rules (detection is pure code/reproducible; rewrite is temp-0 against a pinned model per HARN-06). Pinned to upstream `9c30f79…` (re-vet required on any bump).

**Step 4 — EVAL-GATE.** Independently re-ran: `test_labeled_set_separates_slop_from_human` asserts **recall == 1.0** and **false-positive rate == 0.0** on the seed labeled set → **PASS**. Full engine unit suite: **173 passed**, ruff clean. Importing the cells requires no API key and performs no I/O at import.

```
SEC VERDICT:    APPROVED — ELIGIBLE. Gate steps 1–4 green; provenance pinned.
                Status ELIGIBLE, NOT "IN USE": operator approves adoption + agent
                assignment separately. The deterministic validator (ai_flagger) is
                pure code and safe to wire into the bank on adoption; the rewrite
                cell ships behind the same autonomy/eval gates as any other cell.
Residual risk (for arch/operator): LOW. No net/file/exec/credential surface.
                Pre-strip severity: n/a (nothing to strip). Re-vet on upstream bump.
```

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
