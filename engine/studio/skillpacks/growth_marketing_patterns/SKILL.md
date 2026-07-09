---
name: growth-marketing-patterns
description: Use when a run needs a broad growth/marketing pattern library — CRO, pricing,
  positioning, copy editing, SEO structure, retention — as prompt-only methodology. Re-authored
  from the coreyhaines31 marketing-skills PROMPT content ONLY; the repo's bundled CLIs/tools are
  REJECTED and never vendored or run. Trigger words: CRO, pricing, positioning, retention,
  copy-editing, growth patterns.
upstream: coreyhaines31/marketingskills — skills/ prompt content only (derivative; prompt-only)
pinned: 8bfcdffb655f16e713940cd04fb08891899c47db
license: MIT (upstream LICENSE at the pinned commit); our text is original
status: IN-VETTING scaffold — status governed by docs/skills/registry.md, NOT this file
---

# growth-marketing-patterns (prompt-only skillpack)

OUR authored, **prompt-only** re-write of the marketing *methodology* in the upstream
`coreyhaines31/marketingskills` `skills/` tree (pinned `8bfcdffb655f16e713940cd04fb08891899c47db`,
v2.5.1, MIT). Only the prompt/pattern content is adopted. **No executable upstream artifact is
vendored or run.**

## CRITICAL provenance note — the parent repo's executables are REJECTED

Our registry already carries standing sec verdicts on this exact repo, which this pack does
NOT overturn:

- The repo's **67 bundled Node CLIs under `tools/`** are **REJECTED by default** — they read
  env API tokens, hit data brokers (apollo/zoominfo/clearbit/hunter), and **send real email**
  (resend/sendgrid/postmark). Money/credential/send class. Never vendored, never run here.
- **`ads` / `ad-creative` (`google-ads.js`)** is **REJECTED** — claims direct ad-account
  WRITE (money/destructive).
- `validate-skills.sh` / `validate-skills-official.sh` are upstream build tooling — **not run,
  not vendored.**
- Three sibling skills from this family (`map-your-market`, `where-your-customer-lives`,
  `competitor-pr-finder`) already have their own pattern-only rows; this pack does not
  duplicate their live-network seams (those remain gated behind `engine/research/`).

This pack adopts ONLY the safe prose frameworks. Any data-broker / email-send / ad-write
capability stays REJECTED and, if ever wanted, is re-introduced solely through our own vetted,
scoped, dry-run-first adapter (`vetting-protocol.md`) — never the upstream code.

## What was stripped

Everything executable: the entire `tools/` CLI suite (67 Node CLIs), `ads/ad-creative`
ad-account writer, both `validate-skills*.sh` scripts, and any `.claude-plugin` install
tooling. Surviving surface = prompt-only methodology, re-authored in our own words.

## HARD anti-fabrication guardrail (OURS)

An agent loading this pack MUST NOT fabricate reviews, ratings, testimonials, user/customer
counts, scarcity, urgency, or discounts; all social proof traces to REAL verified facts or is
omitted. No deceptive choice architecture / dark patterns. (Same floor as `marketing-playbook`
and `customer-psychology`.)

## Frameworks adopted (methodology, grounded)

Prompt-only patterns across the upstream categories, applied to the tenant's REAL facts:

- **CRO:** page/onboarding/signup/paywall/popup conversion-rate structure; friction audits that
  CLARIFY, never hide material info.
- **Pricing & offers:** pricing-strategy and offer framing tied to true value, not manipulation.
- **Positioning & product marketing:** `product-marketing` as the foundational reference other
  patterns build on; competitor comparison grounded in verifiable competitor facts.
- **Copy & content:** copywriting, copy-editing, content-strategy, social-content structure.
- **SEO structure:** seo-audit, site-architecture, programmatic-SEO, schema (schema emitted only
  from real data — never fabricated `AggregateRating`).
- **Growth & retention:** referrals, churn-prevention, launch structure.

## Progressive disclosure + dormancy

`loader.load()` returns pack metadata only; prompt-only, no executable entrypoint.
`loader.REGISTERED=False` keeps it off any live code path. See `manifest.json`.

## The gate (do NOT self-certify)

Usability is governed solely by the `growth-marketing-patterns` row in
`docs/skills/registry.md`. This file does not grant use. The parent repo's tool rows stay
REJECTED.
