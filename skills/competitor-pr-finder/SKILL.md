---
name: competitor-pr-finder
description: Use when the research engine needs to mine competitor tattoo studios'
  advertising — which creatives run longest and the ANGLE that makes them work —
  to seed positioning and counter-angles. Pulls competitor ad creatives via the
  Meta Ad Library / Foreplay adapter and returns the angle + hook + format as a
  ready-to-adapt seed. Trigger words: competitor, competitor ads, what's working,
  ad angles, positioning, differentiation, Meta Ad Library, Foreplay.
license: pattern-only derivative — see NOTICE
---

# Competitor PR Finder (tattoo-native)

## Overview

This skill makes the research cell **find the competitor creatives that are
actually working and extract the angle** — the seed for our own positioning /
counter-angle, never a copy of their art. It is the retarget of the upstream
`competitor-pr-finder` pattern (which found press coverage + journalists) onto
the surface that matters for a tattoo studio: **paid + organic ad creatives** in
the Meta Ad Library (via Foreplay). It is **prompt-only** and does **no network
itself** — all access runs through the vetted research adapter (`engine/research/`).

> "competitor-pr-finder: finds where your competitors got covered, which
> journalist wrote it, and the angle that got them in. gives you a ready-to-send
> cold pitch" — Sam_Tech1, r/ClaudeAI (verbatim, winning-strategies-kb.md — the
> upstream pattern we retarget to ad-creative mining)

## When to use

Load when building positioning, a campaign, or differentiation for a tenant and
you need to know **what angles competitors run**. Pairs with `map-your-market`
(client demand) and `where-your-customer-lives` (distribution). Do **not** use it
to copy a competitor's creative — it extracts the *angle*, which the strategist
turns into our own on-voice work.

Conditionally loaded by state: research cells with intent `competitor_creatives`.

## How it runs (through the adapter — never inline network)

```python
from research import ResearchRouter, ResearchQuery, default_registry
router = ResearchRouter.for_sources(pack.research.sources, default_registry())
creatives = router.gather(ResearchQuery(
    intent="competitor_creatives", niche=pack_niche,
    competitor="@rival.studio", tenant_id=pack.tenant_id,
)).creatives
```

The Meta-Ad-Library/Foreplay provider does official-API I/O only (TLS on); the
skill never opens a socket. See `NOTICE` for the strip record.

## Tattoo-native sources (the retarget)

See `references/tattoo-native-sources.md`:

- **Meta Ad Library (via Foreplay, primary)** — competitor studio ads; long
  run-time ≈ a working angle.
- **Instagram** — top-engaged organic reels/posts and their hook/format.

## Method (what good looks like)

1. Resolve the competitor(s) for the tenant (handle/name + locale).
2. Pull their running creatives (via the adapter); rank by run-time / engagement.
3. Extract the **angle** (scarcity, proof/transformation, story), the **hook**,
   and the **format** — keep evidence; do not reproduce their imagery/copy.
4. Set a **match confidence** so the router can flag likely **false positives**
   (name collisions, a different studio) for operator review — never silently
   promote a weak match.
5. Output: `Creative[]` (competitor, channel, angle, hook?, format?, url?,
   confidence, evidence) for the strategist to turn into our own on-voice angle.

## Edge cases

- **Competitor false positives:** low-confidence matches are kept but flagged for
  review (the router emits the note) — never dropped, never auto-trusted.
- **Thin data:** a competitor with no running ads returns empty + a note.
- **ToS / IP:** official APIs only; we extract patterns/angles, not their
  creative. Our output is original and still passes the brand-voice + validator +
  jury + confidence gates.

## Output contract

Returns `Creative[]`. Consumed by the strategist/positioning; not client-facing
and never a verbatim competitor creative.
