# Skill Supply-Chain Vetting Gate (HARD RULE)

**Owner:** sec (security review + sign-off) · **Status:** binding governance · **Applies to:** every third-party Claude skill, plugin, or bundled script before it enters the harness.

> ## THE HARD RULE
>
> **No registry row → no agent use.** A skill may be used by an agent only after it has a row in [`registry.md`](./registry.md) showing (a) sec security sign-off, (b) a complete stripped/sandboxed-items list, (c) a pinned upstream commit, and (d) a passed eval-gold-set gate. Anything else is unadopted and MUST NOT be loaded, referenced, or executed by any agent.
>
> This gate is the **precondition for every skill-adoption bead** (`CustomerAcq-1mk.2` … `1mk.8` and any future one). It cannot be waived per-skill; it can only be changed by editing this document with operator approval.

This is **governance**, not an adoption list. Passing the gate makes a skill *eligible*; the **operator** signs off the actual adopt-list separately (see Decision Authority).

---

## Why this exists (threat model)

A third-party skill is **instructions plus, sometimes, code**. Both are attack surface:

| Surface | Concrete risk (seen in the read-only vet) |
|---|---|
| **Bundled scripts — network** | TLS verification disabled (`ssl._create_unverified_context()` / `CERT_NONE`) → silent MITM / exfiltration. *brand-alchemy `domain_checker.py`*, *map-your-market & where-your-customer-lives `fetch.py`*. |
| **Bundled scripts — credential/env read** | Scripts read `GITHUB_TOKEN` / `.env` and ship `.env.example` → token/secret harvesting. *map-your-market & where-your-customer-lives*. |
| **Bundled scripts — money / destructive writes** | Code that spends real money or writes to live external accounts. *coldoutboundskills* `.ts` (Dynadot bulk domain purchase, live Instantly/Smartlead campaigns); *`ads`/`ad-creative`* `tools/clis/google-ads.js` (ad-account **write**); *coreyhaines31/marketingskills* 67 Node CLIs (apollo/zoominfo/clearbit/hunter data brokers + resend/sendgrid/postmark email **send**). |
| **Instructions — prompt injection / off-policy** | A markdown-only `SKILL.md` can still carry injected instructions, off-brand voice, or directives that defeat our determinism/approval gates. |
| **Provenance** | Alias names in our R&D (e.g. `map-your-market`, `human-tone`) **do not always exist verbatim upstream** — without a pinned commit the thing we reviewed is not the thing that loads. |
| **Generation surface** | Tools that auto-generate executable `SKILL.md` (e.g. Nuwa Skill Distiller) multiply both the injection and the unreviewed-code surface. |

The R&D evidence for every claim above is recorded in the marketing repo at `docs/skills/skills-dos-donts.md` and `docs/skills/winning-strategies-kb.md` (read-only vet of the curated sources). This gate operationalizes that vet into a repeatable, enforced process.

---

## Decision Authority

| Decision | Who |
|---|---|
| Security severity of a finding; what must be stripped/sandboxed; **security sign-off** | **sec** (scores at theoretical maximum) |
| Business-context calibration of severity; accepted-risk vs remediation priority | **arch** |
| Approval of the actual **adopt-list** (which eligible skills we actually use) and acceptance of any residual high/critical risk | **operator** |
| Eval-gold-set pass/fail (Phase-2 `rvy` harness) | **qa / eval harness** (objective gate) |

sec never closes the adoption bead and never calibrates its own scores. A green security sign-off is **necessary but not sufficient** — operator adopt-approval and a passed eval-gate are independent columns.

---

## The 4-Step Gate

Every skill traverses all four steps in order. A step that fails sends the skill to **REJECTED** or **HELD** (see status legend in the registry). Each step writes its output into the candidate's registry row.

### Step 1 — READ (sec reads `SKILL.md` + every shipped script)

- Fetch the upstream skill at a **specific commit** (not a branch/tag that can move). Record the full 40-hex SHA.
- Read `SKILL.md`, every `references/*`, and **every** shipped script (`.py`, `.js`, `.ts`, `.sh`, GitHub Actions, Dockerfiles, Makefiles).
- **No execution during review.** Reading only. Do not `pip install`, `npm install`, or run any setup that triggers code.
- **Obfuscated / minified code:** either fully de-obfuscate and review the result, or **REJECT**. Unreadable code is an automatic fail — we do not sign off on what we cannot read.
- **Instructions-only skills are still read in full** for prompt-injection, off-policy directives, and brand-voice conflicts. "No script" ≠ "no review."
- **Output:** a capability inventory — every network call, file read/write, process exec, env/credential read, money-spending action, and external-account write, each tagged with its classification (below).

### Step 2 — STRIP / SANDBOX (remove every unintended capability; document each removal)

Default posture: **strip**. We vendor the smallest safe surface. Prefer prompt-only (`SKILL.md` + `references/*.md`); strip bundled CLIs/scripts/Actions unless a capability is explicitly intended **and** approved.

Capability classification:

| Class | Default action |
|---|---|
| **Money-spending / destructive external write** (domain purchase, live campaign create, ad-account write, bulk email **send**) | **REJECT** the script. Mine patterns only; never vendor the executor. Re-introduce only via our own harness behind the human approval + autonomy gate, never the upstream code. |
| **Credential / env read** (`GITHUB_TOKEN`, `.env`, broker API tokens) | **Strip**. If a capability genuinely needs a credential, it runs through our scoped-cred + `--dry-run` path, never the skill's own env read. |
| **Network — TLS disabled** | **Strip**. If the capability is wanted, re-author it through our vetted fetch adapter (Firecrawl / Meta Ad Library) with **TLS verification restored**; never run the upstream fetch. |
| **Network — TLS verified, to a known host** | **Sandbox + allowlist** the host, or route through our adapter. No raw outbound by default. |
| **File write outside the skill's own scratch** | **Strip / sandbox** to a bounded scratch path. |
| **Process exec / shell-out** | **Strip** unless explicitly intended; if kept, no shell string interpolation, args allowlisted. |
| **Pure prompt / deterministic transform, no I/O** | **Allow** (still read for injection/off-policy). |

**Document every removal** in the row's *What was stripped/sandboxed* field: file + line/function, the capability, why it was unintended, and the action taken (stripped / sandboxed / allowlisted). "Reviewed, nothing to strip" is itself a valid, recorded result for prompt-only skills.

### Step 3 — RE-AUTHOR + PIN

- Re-author the surviving surface into **our Agent Skills format**, applying our **brand-voice** rules and **determinism rules** (temperature-0 decision/classify, pinned models, no hidden nondeterminism — consistent with `harness/config.py` HARN-06).
- Replace any stripped network/exec capability with a call into our vetted adapter, never the upstream code.
- **PIN the upstream commit SHA** (full 40-hex) in the row. The pin is the provenance anchor: re-vet is required on any bump (Step-4 must re-run). Record our re-authored artifact path under `engine/skills/<name>/` (or the agreed skills dir) — this is the *only* version an agent loads.

### Step 4 — EVAL-GATE → REGISTER

- Run the re-authored skill through the **Phase-2 eval gold-set gates** (`CustomerAcq-rvy` eval spine). It must meet the gate thresholds for its task before it is eligible.
- Only when Steps 1–4 are green **and** the operator has approved adoption does sec add/complete the registry row. Eligibility (gate green) and adoption (operator-approved, agent assigned) are tracked as distinct columns so an eligible-but-not-yet-adopted skill is never silently used.

---

## Edge cases (required handling)

- **Obfuscated / minified script** → de-obfuscate fully or REJECT. No sign-off on unreadable code.
- **Calls an external API** → sandbox + allowlist the host, or strip and route through our adapter. Never raw upstream network.
- **Version drift** → the pinned commit is authoritative. Any upstream bump invalidates the row; re-run Steps 1–4 before the new version is used. CI/registry check fails a skill whose loaded artifact does not match its pinned, re-vetted version.
- **Instructions-only skill** → still fully read for prompt-injection / off-policy / brand-voice conflict before sign-off.
- **No upstream LICENSE** (e.g. a fork shipping none) → add a LICENSE consistent with lineage before redistributing; record license status in the row.
- **"First-party Anthropic marketing plugin"** claims → verify provenance. No such official plugin exists; community-authored "marketing" plugins are mislabeled and get the full gate.

---

## Enforcement

1. **Precondition:** every adoption bead (`1mk.2`…`1mk.8`+) is BLOCKED by this bead. An agent acquires a skill only by pointing at a complete registry row.
2. **No row → no use:** an agent that cannot cite a registry row for a skill MUST NOT load, reference, or execute it. This includes bundled scripts.
3. **Loaded == pinned == vetted:** the artifact an agent loads must be our re-authored copy at the pinned, re-vetted version. A mismatch (drift) is a gate failure.
4. **Re-vet on bump:** changing the upstream pin re-opens Steps 1–4.
5. **Audit:** the registry is the single source of truth for "what may be used, by whom, at what version, with what stripped." sec maintains it; arch calibrates; operator approves the adopt-list.

---

## sec sign-off record (template — copied into each registry row's detail)

```
Skill:              <our-name>  (upstream: <org/repo/path>)
Pinned commit:      <full 40-hex SHA>            # REQUIRED; alias names are not provenance
Reviewed by:        sec        Date: <YYYY-MM-DD>
Step 1 READ:        SKILL.md + <N> scripts read in full; obfuscation: none/de-obfuscated/REJECT
Capability inventory + classification: <list>
Step 2 STRIP:       <file:loc — capability — why unintended — action>  (one line per removal)
Step 3 RE-AUTHOR:   our-format path = engine/skills/<name>/ ; brand-voice + determinism applied
Step 4 EVAL-GATE:   gold-set = <id> ; result = PASS/FAIL/PENDING
SEC VERDICT:        APPROVED-AS-STRIPPED / REJECTED / HELD-PENDING-<reason>
Residual risk (for arch/operator): <none / description + max-severity score>
```
