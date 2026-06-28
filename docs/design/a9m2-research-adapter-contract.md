# a9m.2 (RSCH-01) — Pluggable research adapter contract (PROPOSED)

**Status:** PROPOSED contract, **not yet claimed** (a9m.2 is dep-blocked on the
a9m.1 ADR). Authored by **growth** to be build-ready the moment a9m.1 signs off.
**arch:** please fold the interface below into `docs/adr/phase-3-content-engine.md`
(or flag deltas) so the Phase-3 slice builds to one contract.

**Builds on** the Phase-2 research adapter already on `main` (bead 1mk.4,
`engine/research/`): the `SourceProvider` protocol, `ResearchRouter`, and the
network-safety gate (`research/safety.py`: TLS-in-code, official-API-only, SSRF
guard, rate limits — PR #50). a9m.2 = that foundation **+ budget cap + formal
mock mode + Exa + Foreplay-primary/Meta-fallback, Reddit OUT, scored normalized
result.**

---

## 1. Backends (one interface, multiple)

| Backend | Role | Notes |
|---|---|---|
| **Exa** | semantic search / discovery (web) | NEW vs 1mk.4 |
| **Firecrawl v2** | scrape / crawl (web) | carries over from 1mk.4 |
| **Foreplay Competitor Advertising API** | **PRIMARY** competitor ads | operator has access (~10k free credits/mo) |
| **Meta Ad Library (free)** | **FALLBACK** competitor ads | only when Foreplay is down/over-budget |
| ~~Reddit~~ | **OUT of the MVP brain** | explicitly excluded (a9m.2 AC) |

> Note: 1mk.4 used r/tattoos as a tattoo-native channel. For the **Phase-3 MVP
> brain, Reddit is OUT** — the a9m.2 adapter ships without a Reddit backend. (The
> 1mk.4 skills' Reddit framing is retargetable later; not wired here.)

## 2. The interface

```python
class ResearchAdapter:
    def __init__(self, providers, *, budget: Budget, mode: Mode = Mode.MOCK): ...
    def run(self, query: ResearchQuery) -> ResearchResult: ...
```

- Reuses the 1mk.4 `SourceProvider` protocol (`name`, `channels`, `gather`,
  optional `fetch`) for each backend, plus a `cost_estimate(query)` so the budget
  cap can pre-check before a paid call.
- `run()` fans out across backends (router), normalizes + dedupes + **scores**,
  enforces the **budget cap**, and degrades on any dead/over-budget backend.

## 3. Hard budget cap (enforced in code)

```python
@dataclass(frozen=True)
class Budget:
    max_calls: int            # per run
    max_credits: float        # per run (Foreplay/Exa credits)
    max_wall_seconds: float    # per run
```

- Checked **before each paid call** (`cost_estimate`) and accumulated after.
- **Over budget → stop calling, return what we have, set `over_budget=True`.**
  Never blocks, never hangs, never overspends (a9m.2 AC).
- Budget is per-run; the caller (idea/angle cell) passes the tenant's cap.

## 4. Mock / recorded mode (default)

```python
class Mode(Enum): MOCK = "mock"; LIVE = "live"
```

- **`MOCK` is the default.** Fixtures (recorded backend responses) → the slice +
  CI run with **zero live calls**.
- **Secrets absent (CI) → MOCK auto-selected**; LIVE never hard-errors the build.
- `LIVE` requires the keys (from the pack secret/env) **and** passes the
  `research/safety.py` gate; live providers carry the **HARD sec re-vet-before-live**
  gate (TLS-in-code, key-from-pack, SSRF guard, official-API-only, rate limits —
  already enforced in code, PR #50).

## 5. Normalized result (what idea/angle cells consume)

```python
@dataclass(frozen=True)
class ScoreBreakdown:
    """RESERVED (arch Decision 1a) — Phase-7 weightable dimensions. Optional now
    so adding it later is NOT a breaking contract change. a9m.2 leaves it None."""
    relevance: float | None = None
    recency: float | None = None
    authority: float | None = None

@dataclass(frozen=True)
class ResearchItem:
    source: str          # backend name
    kind: str            # "signal" | "angle" | "competitor_creative"
    text: str
    url: str | None
    score: float         # 0..1 single relevance/quality float — the MVP ranking key
    evidence: tuple[str, ...]
    breakdown: ScoreBreakdown | None = None  # RESERVED (Decision 1a); None in a9m.2

@dataclass(frozen=True)
class ResearchResult:
    query: ResearchQuery
    items: tuple[ResearchItem, ...]
    sources_used: tuple[str, ...]
    over_budget: bool = False
    degraded: tuple[str, ...] = ()   # backends that failed/were skipped
    notes: tuple[str, ...] = ()
```

- **Junk / non-normalizable → dropped**, not poisoning the result.
- **Zero results is valid** — the idea/angle cells fall back to brand context only
  (must not crash on an empty set; a9m.2 + a9m.4 contract).

## 6. Degradation semantics

- A backend down / rate-limited / over-budget → skipped, recorded in `degraded`,
  the run **continues on the others** with a flag. One dead source never fails the
  run (a9m.2 AC).

## 7. Verification (maps to a9m.2 AC)

- **Mock run** returns a normalized result from fixtures with **0 live calls**.
- **Budget test**: a capped run stops at the cap, returns partial, `over_budget=True`.
- **Degradation test**: a stubbed-dead backend leaves the run succeeding on others.
- **No Reddit backend** present. **Foreplay primary**, Meta Ad Library fallback.

## 8. Reuse map (so the build is mechanical)

| a9m.2 piece | From 1mk.4 (on main) | New in a9m.2 |
|---|---|---|
| `SourceProvider` protocol | ✅ `research/adapter.py` | + `cost_estimate` |
| fan-out / merge / dedupe | ✅ `research/router.py` | + scoring, + budget accounting, + `degraded` |
| safety gate (TLS/SSRF/official/rate/key) | ✅ `research/safety.py` (PR #50) | (unchanged; live re-vet) |
| mock provider | ✅ `FixtureProvider` | → formal recorded-fixtures `MOCK` mode |
| Firecrawl / Meta-Ad-Library seams | ✅ `research/providers/` | + Exa provider; Foreplay-primary wiring |
| Budget / Mode / ResearchResult(scored) | — | NEW |

---

**Scoring — RESOLVED (arch Decision 1a, a9m.1 ADR).** a9m.2 ships the **single
`score` float** as the ranking key; the optional `ScoreBreakdown` sub-object
(`scores`) is **reserved now and left `None`**, so Phase-7 can add weightable
dimensions with **no breaking contract change**. Nothing in a9m.2 is blocked on
per-dimension. This doc is referenced from the a9m.1 ADR as the source contract.

**Hand-off:** growth holds this contract for super to dispatch once a9m.1 ADR
signs off. Not claiming a9m.2.
