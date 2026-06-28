# Research adapter (bead 1mk.4)

The vetted seam between the three adopted research skills and the network.

## Why this exists

The skills `map-your-market`, `where-your-customer-lives`, and
`competitor-pr-finder` are **prompt-only methodology** (see `skills/*/SKILL.md`).
The upstream versions shipped a `fetch.py` that **disabled TLS verification**
(`ssl._create_unverified_context()` / `CERT_NONE`) and read `GITHUB_TOKEN` /
`.env`. Per `docs/skills/vetting-protocol.md` that script is **stripped — never
vendored**. All fetching instead goes through a vetted `SourceProvider`:

- **Firecrawl** — official-API web/social fetch (r/tattoos, Instagram hashtags,
  Pinterest, TikTok, artist sites), **TLS verified**.
- **Foreplay / Meta Ad Library** — competitor ad creatives + the winning angle.

**Rules:** official APIs only, no scraping bans, TLS always on, secrets from the
tenant pack (`[secrets.*]`) — never inline, never a vendored `.env`.

## Shape

```
research/
  adapter.py     # contract: Channel, ResearchQuery, Signal/Community/Creative/
                 # Document/ProviderResult/ResearchResult, SourceProvider protocol
  router.py      # ResearchRouter: pick vetted providers by channel, fan out,
                 # merge+dedupe, enforce limit, flag thin-data + false positives
  providers/
    fixture.py          # deterministic, offline — tests + safe default
    firecrawl.py        # SEAM: eng wires the live official API (raises until then)
    meta_ad_library.py  # SEAM: eng wires Foreplay/Meta Ad Library (raises until then)
```

## Intents → channels (the tattoo-native retarget)

| Skill | Intent | Default channels |
|---|---|---|
| map-your-market | `map_market` | r/tattoos, Instagram hashtag, TikTok, Pinterest |
| where-your-customer-lives | `find_communities` | r/tattoos, Instagram hashtag, TikTok |
| competitor-pr-finder | `competitor_creatives` | Meta Ad Library, Instagram hashtag |

## Usage (research engine / a research cell)

```python
from research import ResearchRouter, ResearchQuery, default_registry

router = ResearchRouter.for_sources(pack.research.sources, default_registry())
result = router.gather(ResearchQuery(
    intent="map_market", niche="fine-line, Brooklyn",
    seed_terms=("#fineline", "#brooklyntattoo"), tenant_id=pack.tenant_id,
))
# result.signals / .communities / .creatives  (+ .notes, .sources_used)
```

The router does **no network** — it only orchestrates vetted providers, so an
un-vetted source name in a pack can never be reached (it is dropped with a note).

## What eng wires (coordination point)

`default_registry(use_fixture=True)` maps the live source names to the fixture so
the engine runs end-to-end now. To go live:

1. Implement `FirecrawlProvider.fetch` / `.gather` against the official Firecrawl
   API with **TLS verification ON** (the explicit replacement for the stripped
   `fetch.py`). Key from the pack secret / env.
2. Implement `MetaAdLibraryProvider.gather` against Foreplay (primary) / the Meta
   Ad Library API; set `Creative.confidence` from match strength so the router
   flags likely false positives.
3. Flip the engine to `default_registry(use_fixture=False)` (or inject the live
   providers) once both pass their own eng tests.
4. Wire `ResearchRouter.gather` into the research cell (the `ResearchNode` seam in
   `harness/nodes.py` — eng2's HARN-02 typed cell). The fixture keeps everything
   working until then; no behavior change required to adopt the seam.

Until step 4, the router degrades cleanly: an un-wired live provider is skipped
with a note, never an exception.

## Live-go security gate (HARD — sec re-vet before any live provider ships)

Enforced in `research/safety.py`; the live providers are wired through it so the
conditions cannot be skipped:

- **TLS-in-code** — `assert_safe_url` rejects any non-`https://` target; the
  upstream TLS-disabled `fetch.py` stays stripped.
- **official-API-only** — `assert_official_endpoint` allowlists the API base host
  per provider (`api.firecrawl.dev`; `api.foreplay.co` / `graph.facebook.com`).
- **SSRF guard on `fetch(url)`** — `assert_safe_url` blocks private / loopback /
  link-local / reserved / cloud-metadata targets and URLs with embedded
  credentials. `FirecrawlProvider.fetch` runs it **before** anything else; the
  live client must additionally re-check the *resolved* IP after DNS.
- **rate limits** — every provider carries a `RateLimiter` token bucket (respect
  source ToS / API caps).
- **key-from-pack** — providers take the key in their constructor from the tenant
  pack secret / env, never a vendored `.env`, never `GITHUB_TOKEN`.

sec must re-vet these on the live wiring before the providers leave MOCK/fixture
mode (recorded against bead 1mk.4; carries forward to the Phase-3 a9m.2 adapter).

## Eval

Research output quality is gated against a gold set (Phase-2 `rvy`). A smoke set
lives at `evals/gold/research-niche-smoke.jsonl`; the registry eval-gate for
these skills is `PENDING-on-gold-set` until the holdout + gate land.
