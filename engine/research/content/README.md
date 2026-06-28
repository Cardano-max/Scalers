# Content-engine research adapter (bead a9m.2 / RSCH-01)

The Phase-3 content brain's external-signal layer. One pluggable interface over
many backends, **budget-capped**, **MOCK by default**, returning a typed scored
`ResearchResult` for the idea/angle cells. Built per the merged contract
(`docs/design/a9m2-research-adapter-contract.md`, ADR-referenced), reusing the
1mk.4 foundation (`research/adapter.py` `SourceProvider`, `research/safety.py`).

## Backends

| Backend | Role | Reddit? |
|---|---|---|
| Exa | semantic search / discovery (web) | — |
| Firecrawl | scrape / crawl (web) | — |
| **Foreplay** | **PRIMARY** competitor ads | — |
| Meta Ad Library | **FALLBACK** competitor ads (free) | — |
| ~~Reddit~~ | **OUT of the MVP brain** | excluded |

The Foreplay→Meta fallback only spends the fallback if Foreplay degrades or
returns nothing.

## Usage

```python
from research.content import build_adapter, Budget, Mode
from research.adapter import ResearchQuery

adapter = build_adapter(mode=Mode.MOCK, budget=Budget(max_calls=8, max_credits=20))
res = adapter.run(ResearchQuery(intent="competitor_creatives",
                                niche="fine-line, Brooklyn", competitor="@rival"))
# res.items: tuple[ResearchItem]  (score-ranked; .breakdown reserved=None)
# res.over_budget / res.degraded / res.sources_used / res.mode
```

`build_adapter()` is **MOCK by default** and **auto-MOCK when no keys** are passed
(secrets absent → CI/dev never hard-errors). LIVE wires the real seams (Exa /
Firecrawl / Foreplay / Meta), which raise until eng implements them — LIVE then
**degrades** cleanly (empty + `degraded`), it does not crash.

## Budget cap (hard, per run)

`Budget(max_calls, max_credits, max_wall_seconds)` — checked **before each paid
call**; over budget → stop calling, return what we have, `over_budget=True`. Never
blocks, hangs, or overspends. A field of `0` means unlimited on that axis.

## Scoring (arch Decision 1a)

`ResearchItem.score` is a single relevance float (the ranking key). The optional
`ScoreBreakdown` sub-object (field `breakdown`) is **reserved** and left `None` so
Phase-7 weightable dimensions land with no breaking change.

## Go-live gate (sec)

LIVE providers carry the HARD sec gate (`research/safety.py`): TLS-in-code,
official-API-only, **SSRF guard incl. F1 obfuscated-IP + F2 resolved-IP recheck**,
rate limits, key-from-pack. They stay behind it until sec re-vets the live wiring.

## Tests

`engine/tests/test_research_content_adapter.py` — MOCK 0-live-calls, budget
call/credit caps → partial+`over_budget`, dead-backend degradation, Foreplay
primary / Meta fallback, no-Reddit, zero-results valid, LIVE-degrades-not-crashes.
DB-free, hermetic.
