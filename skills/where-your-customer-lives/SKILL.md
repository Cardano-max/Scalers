---
name: where-your-customer-lives
description: Use when the research engine needs to find WHERE a tattoo studio's
  clients actually gather and how to enter each place authentically. Takes the
  tenant's ICP and returns the tattoo-native communities (subreddits, Instagram
  hashtag clusters, Pinterest, TikTok tags) plus a per-channel entry tactic.
  Trigger words: where are my customers, communities, channels, where to post,
  distribution, audience location, entry tactics.
license: pattern-only derivative — see NOTICE
pinned: 8bfcdffb655f16e713940cd04fb08891899c47db  # family ref (ORIGINAL/pattern-only; not verbatim upstream)
---

# Where Your Customer Lives (tattoo-native)

## Overview

This skill makes the research cell **locate the communities a tenant's clients
inhabit** and return a concrete **entry tactic** for each — not a generic "post
on social" answer. It is the retarget of the upstream `where-your-customer-lives`
pattern (which searched Reddit/HN/DuckDuckGo) onto tattoo-native surfaces, and it
is **prompt-only**: it does **no network itself**. All access runs through the
vetted research adapter (`engine/research/`).

> "where-your-customer-lives: give it your ICP, it searches Reddit/HN/DuckDuckGo
> to find the actual communities your customers are in. per-channel entry
> tactics" — Sam_Tech1, r/ClaudeAI (verbatim, winning-strategies-kb.md)

## When to use

Load when planning **distribution** for a tenant: which communities to show up in
and how. Pairs with `map-your-market` (what clients want) — this answers *where*.
Do **not** use it to write the posts (copywriter) or to mine competitors
(`competitor-pr-finder`).

Conditionally loaded by state: research cells with intent `find_communities`.

## How it runs (through the adapter — never inline network)

```python
from research import ResearchRouter, ResearchQuery, default_registry
router = ResearchRouter.for_sources(pack.research.sources, default_registry())
communities = router.gather(ResearchQuery(
    intent="find_communities", niche=pack_niche,
    seed_terms=("#blackwork", "brooklyn tattoo"), tenant_id=pack.tenant_id,
)).communities
```

The upstream `fetch.py` (TLS disabled, `GITHUB_TOKEN`/`.env` read) is **stripped**
— see `NOTICE`.

## Tattoo-native communities (the retarget)

See `references/tattoo-native-sources.md`:

- **Subreddits** — r/tattoos, r/TattooDesigns: answer questions with genuine
  value, never a pitch.
- **Instagram hashtag clusters** — style + **city/locale** tags; geo-tag so local
  clients surface the work.
- **Pinterest** — publish flash as pins so saves drive discovery.
- **TikTok** — process/reveal tags + sounds; pin a "how to book" comment.

## Method (what good looks like)

1. From the ICP (style, city, client type), enumerate candidate communities per
   channel (via the adapter).
2. For each, write a **specific entry tactic** (how to add value there without
   being salesy) — the per-channel tactic IS the deliverable.
3. Rank by reachability + fit; flag thin/locale gaps.
4. Output: `Community[]` (name, channel, entry_tactic, url?, size_hint) for the
   strategist + scheduler.

## Edge cases

- **Thin niche / small city:** return what exists + a note; suggest the nearest
  larger community. Never fabricate a community.
- **ToS / rate limits:** official APIs only — enforced at the adapter.
- Entry tactics keep the human approval gate: nothing auto-posts.

## Output contract

Returns `Community[]`. Consumed by the strategist + scheduler; not client-facing.
