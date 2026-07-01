---
name: marketing-playbook
description: Use when a run needs grounded go-to-market craft — content strategy, full-funnel
  demand generation, and product-marketing (ICP/positioning/messaging/launch). Prompt-only
  frameworks re-authored from a vetted upstream marketing-skill collection, wrapped in OUR
  hard anti-fabrication + anti-dark-pattern guardrail. Trigger words: positioning, ICP,
  messaging, funnel, demand-gen, launch, content-strategy, GTM.
upstream: alirezarezvani/claude-skills — marketing-skill (derivative; prompt-only re-author)
pinned: 711ae3108832a98a0539101b46280de23bc0a1d4
license: upstream repo license (see upstream LICENSE at the pinned commit); our text is original
status: IN-VETTING scaffold — status governed by docs/skills/registry.md, NOT this file
---

# marketing-playbook (prompt-only skillpack)

OUR authored, **prompt-only** re-write of the go-to-market frameworks in the upstream
`marketing-skill` collection (`alirezarezvani/claude-skills`, path `marketing-skill/`,
pinned `711ae3108832a98a0539101b46280de23bc0a1d4`). No upstream code is vendored — every
bundled script was **stripped** (see "What was stripped"). This pack ships methodology the
supervisor/agents can load; it introduces no network/file/exec capability.

## Provenance + pin

Derivative, prompt-only. `pinned:` above is the real upstream commit that last touched the
`marketing-skill/` subtree, verified via the GitHub API at vetting time (2026-07-01). On any
upstream bump the pack is **frozen** until re-vetted (registry rule). The registry row's pin
equals this `pinned:` field.

## What was stripped (NOT ported — never run)

The upstream collection bundles ~59 Python scripts under each skill's `scripts/` dir (e.g.
`brand_voice_analyzer.py`, `seo_optimizer.py`, `calculate_cac.py`, `schema_generator.py`)
and a `CLAUDE.md` that instructs `python3 <skill>/scripts/<tool>.py`. **None of these are
vendored or executed.** Upstream claims "stdlib-only / demo mode", but per our supply-chain
gate we do not run or port third-party scripts. Any of that computation (CAC math, schema
emission) is re-introduced later ONLY through our own vetted adapter (`vetting-protocol.md`).

## HARD anti-fabrication + anti-dark-pattern guardrail (OURS, non-negotiable)

The upstream psychology material presents persuasion levers (scarcity, social proof, decoy
pricing, loss aversion) as amoral technique with inconsistent ethical caveats. This pack
overrides that with a hard floor — an agent loading this pack MUST obey:

- **Never fabricate.** No invented testimonials, review counts, star ratings, customer
  logos, user counts, urgency, discounts, or scarcity. Every claim of social proof or
  scarcity must trace to a REAL, verified fact supplied for the run. No signal → do not make
  the claim. This mirrors the `customer-psychology` no-fabrication rule.
- **No deceptive choice architecture.** Do not design decoy tiers, hidden fees, forced
  continuity, confirmshaming, or friction-hiding that obscures material information. Choice
  architecture may CLARIFY, never mislead.
- **No fake urgency.** Only surface a deadline/limit that is genuinely true.
- **Schema/JSON-LD:** never emit `AggregateRating`/`Review` values (rating, reviewCount) that
  are not sourced from real, verified review data. (Upstream `local-seo-manager`'s pinned
  commit exists specifically to stop fabricated aggregate ratings — we keep that guarantee and
  do not generate rating schema from nothing.)

## Frameworks adopted (methodology, grounded)

**Content strategy:** brand-voice/tone + readability discipline; platform-native structure
(LinkedIn, X, Instagram, TikTok, YouTube); content templates as scaffolds to be filled with
the tenant's REAL facts, never generic filler.

**Demand generation:** full-funnel awareness→consideration→conversion; multi-channel
(LinkedIn, Google/Meta Ads, SEO, partnerships); CAC and channel benchmarking framed as
"compute from the tenant's real spend/results", not asserted; attribution literacy.

**Product marketing:** ICP definition; positioning (April Dunford's competitive-alternatives
framing); a messaging hierarchy (value → pillars → proof); launch playbook structure;
battlecards + objection handling grounded in real competitor facts (no invented competitor
claims); win/loss synthesis.

**Buyer psychology (levers, used honestly):** Jobs-to-be-Done, endowment, anchoring, Rule of
100, charm pricing, AIDA, Hick's/Fogg — applied only to TRUE product attributes and framed
transparently per the guardrail above.

## Progressive disclosure + dormancy

`loader.load()` returns pack metadata only; the pack is **prompt-only** and has no executable
entrypoint (nothing to call — the value is the methodology text). `loader.REGISTERED=False`
keeps it off any live code path regardless of registry status. See `manifest.json`.

## The gate (do NOT self-certify)

Usability is governed solely by the `marketing-playbook` row in `docs/skills/registry.md`.
This file does not grant use.
