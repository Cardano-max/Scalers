---
name: research-ops
description: Use when a run needs disciplined market/product research — TAM/SAM/SOM sizing by
  triangulation, survey sample-size rigor, and segmentation scoring — with method + assumptions
  + confidence shown for every number and a hard no-fabrication rule. Prompt-only, re-authored
  from a vetted upstream research-ops collection. Trigger words: TAM, SAM, SOM, market sizing,
  segmentation, sample size, research-ops, product research.
upstream: alirezarezvani/claude-skills — research-ops (derivative; prompt-only re-author)
pinned: a088c8ba778319fea026c8a25e9e98b15754f379
license: upstream repo license (see upstream LICENSE at the pinned commit); our text is original
status: IN-VETTING scaffold — status governed by docs/skills/registry.md, NOT this file
---

# research-ops (prompt-only skillpack)

OUR authored, **prompt-only** re-write of the research methodology in the upstream
`research-ops` collection (`alirezarezvani/claude-skills`, path `research-ops/`, pinned
`a088c8ba778319fea026c8a25e9e98b15754f379`). No upstream code is vendored — every bundled
script was **stripped**. The pack introduces no network/file/exec capability.

## Provenance + pin

Derivative, prompt-only. `pinned:` is the real upstream commit that last touched the
`research-ops/` subtree, verified via the GitHub API at vetting time (2026-07-01). Frozen on
any upstream bump until re-vetted. The registry row's pin equals this field.

## What was stripped (NOT ported — never run)

Upstream bundles deterministic Python tools (`market_sizer.py`, `sample_size_planner.py`,
`segmentation_scorer.py`) plus ops scripts (`onboard.py`, `config_loader.py`,
`ar_evaluator.py`) and an opt-in "autoresearch bridge". Upstream describes them as
"stdlib-only, deterministic, no LLM calls" — **we still do not run or vendor them.** Only the
methodology text is adopted. The numeric tooling (sizing triangulation, sample-size
computation) may be re-introduced later ONLY via our own vetted adapter, never the upstream
scripts.

## Why this one is high-value + low-risk

Upstream's own governance is strong and aligns with our house anti-fabrication stance — we
keep and restate its rules rather than override them:

- **Never fabricate research or user insight.** No invented interview quotes, survey results,
  or figures. No signal → say "insufficient evidence", never guess.
- **Every number carries method + assumptions + confidence.** A TAM is never a single number
  with no method — always triangulate top-down vs. bottoms-up and flag divergence.
- **No spurious precision** (e.g. "$3.7142B") — size to the decision's tolerance.
- **Sample size is per-segment**, not "powered only in aggregate"; apply finite-population
  correction and per-segment floors.
- **No leading/biased survey questions** — pre-test against known bias patterns.

## Frameworks adopted (methodology, grounded)

**Market research:** TAM/SAM/SOM three-tier sizing (top-down AND bottoms-up, triangulated);
segmentation scored against Kotler's five criteria (measurable, substantial, accessible,
differentiable, actionable).

**Product research:** user-research synthesis with saturation guidance (know when enough
signal is reached); insight must trace to real observed evidence.

**Research governance:** evidence-first planning; named stakeholder sign-off; transparent
assumptions; method disclosure before numeric outputs. (Upstream also ships
`clinical-research` and `research-finance` sub-skills — out of our marketing scope, adopted as
methodology reference only, not as capability.)

## Progressive disclosure + dormancy

`loader.load()` returns pack metadata only; the pack is **prompt-only** with no executable
entrypoint. `loader.REGISTERED=False` keeps it off any live code path. See `manifest.json`.

## The gate (do NOT self-certify)

Usability is governed solely by the `research-ops` row in `docs/skills/registry.md`. This file
does not grant use.
