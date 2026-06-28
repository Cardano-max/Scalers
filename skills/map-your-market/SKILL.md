---
name: map-your-market
description: Use when the research engine needs to map tattoo-market demand — the
  pains, desires, and angles real clients voice — before strategy or copy. Mines
  tattoo-native sources (r/tattoos, Instagram hashtags, Pinterest, TikTok) for
  recurring pain/demand signals and turns them into ICP notes + messaging angles
  the strategist can score. Trigger words: market research, demand, pain points,
  angles, ICP, what do clients want, audience research.
license: pattern-only derivative — see NOTICE
---

# Map Your Market (tattoo-native)

## Overview

This skill makes the research cell **mine where tattoo demand actually surfaces**
and return it as structured signals, not a generic SEO keyword dump. It is the
retarget of the upstream `map-your-market` pattern (which searched
Reddit/HN/GitHub/G2) onto **tattoo-native** sources, and it is **prompt-only**:
it does **no network itself**. Every fetch goes through the vetted research
adapter (`engine/research/`) — official APIs, TLS on.

> "map-your-market: searches Reddit, HN, GitHub Issues, G2 for pain signals.
> outputs ICP definition and messaging angles" — Sam_Tech1, r/ClaudeAI
> (verbatim, winning-strategies-kb.md — the upstream pattern we retarget)

> "where the people getting the most value ... treat it like a thinking/workflow
> partner instead of just a copy generator." — Appropriate-Sir-3264 (KB)

## When to use

Load this skill when a research run needs a **demand map** for a tenant's niche:
before angle selection, a campaign, or a positioning refresh. Do **not** use it
to write copy (that's the copywriter skill) or to find communities (that's
`where-your-customer-lives`).

Conditionally loaded by state: the harness loads it on research cells whose
intent is `map_market`.

## How it runs (through the adapter — never inline network)

The skill emits a `ResearchQuery(intent="map_market", …)`; the engine's
`ResearchRouter` dispatches to the tenant's vetted providers and returns merged,
deduped `Signal`s. **The skill never opens a socket.** The upstream `fetch.py`
(TLS disabled, `GITHUB_TOKEN`/`.env` read) is **stripped** — see `NOTICE` +
`docs/skills/vetting-protocol.md`.

```python
from research import ResearchRouter, ResearchQuery, default_registry
router = ResearchRouter.for_sources(pack.research.sources, default_registry())
signals = router.gather(ResearchQuery(
    intent="map_market", niche=pack_niche,
    seed_terms=("#fineline", "#brooklyntattoo", "cover up"),
    tenant_id=pack.tenant_id,
)).signals
```

## Tattoo-native sources (the retarget)

See `references/tattoo-native-sources.md`. In short:

- **r/tattoos, r/TattooDesigns** — "is this price fair", "first tattoo", regret /
  cover-up threads → **pain + demand** signals.
- **Instagram hashtags** — style tags (`#fineline`, `#blackwork`) + city tags →
  what clients save/ask in comments.
- **Pinterest** — saved flash / style boards → **demand** (what they want next).
- **TikTok** — process/reveal tags + sounds → **angle** (formats that pull).

## Method (what good looks like)

1. Pull recent items per channel for the niche + seed terms (via the adapter).
2. Cluster into **pain / demand / angle**; keep the client's own phrasing as
   evidence (don't paraphrase the signal away — see the winning-strategies KB).
3. Score each signal by recurrence + recency (the provider sets `confidence`).
4. Output: an ICP note (who + what they want + what they fear) and a ranked list
   of **messaging angles** for the strategist to pick from — never final copy.

## Edge cases

- **Thin niche data:** return the signals you have + a "thin data" note; never
  invent demand. (The router emits this note automatically.)
- **ToS / rate limits:** official APIs only, no scraping — enforced at the
  adapter, not here.
- Signals are grounding for humans + the strategist; nothing here ships without
  review and the downstream validator/jury/confidence gates.

## Output contract

Returns `Signal[]` (`text`, `channel`, `kind∈{pain,demand,angle}`, `confidence`,
`url?`, `evidence`). The strategist/copywriter consume these; they are not
client-facing.
