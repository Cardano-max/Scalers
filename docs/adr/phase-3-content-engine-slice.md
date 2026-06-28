# ADR: Phase-3 Content-Engine Slice Architecture

- **Status:** Accepted (interfaces/contracts for pm to groom into beads; arch + operator sign off at completion)
- **Date:** 2026-06-28
- **Owner:** arch
- **Phase:** 3 — First vertical slice (posting, mock tooling) · **Requirements:** POST-01, POST-02, RSCH-01, KNOW-02
- **Aligns to:** [`systemdesign.md`](../systemdesign.md) §2 (spine) / §3 (side-effect boundary) / §6 (cell interfaces), [`stack-decision.md`](../stack-decision.md) (Pydantic-AI cells, Agent Skills, KB grounding, mock-first), [`spec.md`](../spec.md) §3 (posting behavior) / §5 (targets), [`adr/phase-2-eval-spine.md`](./phase-2-eval-spine.md) (every cell is eval-able)
- **Builds on (does not reinvent):** `cells/base.py` (`Cell`), `cells/validators.py` (`ValidatorBank`), `cells/content_brief.py`, `autonomy/produce.py` (`produce_and_record_decision`, `resolve_channel_policy`), `autonomy/jury.py` (stub jury), `harness/router.py` (`route`), `harness/state.py` (`GraphState`, `Gate`), `kb/store.py` (`KbStore`), `sideeffects/boundary.py` (`SideEffectBoundary`) + `sideeffects/keys.py`, `config/schema.py` (`TenantPack`, `Channel`, `VoiceRef`), and the **just-merged 2kp** routing (`phase1_slice.py:slice_route` / `_SIDE_EFFECT_CHANNEL`).

This ADR fixes the **content-engine slice**: the first real engine producing a brand-voiced organic post draft that lands in the review queue (or auto-publishes via a **mock** connector). It is the template the other two engines (outreach, engagement) will mirror in Phase 7. It composes the now-in-use **Tier-1 brand-voice** and **AI-flagger** Agent Skills. Pure decision/interface doc — **no implementation**; the interfaces below are what pm grooms into beads and engineers build to.

---

## Context & scope

Phase-3 success criteria (roadmap): (1) research→strategy→create produces a post draft **grounded in brand-voice from the KB**; (2) media/format validation rejects out-of-spec creatives **in code**; (3) basic Exa/Firecrawl research under a **budget cap**; (4) the produced action is **persisted with a confidence score, ready for the console**.

What stays **mock** in Phase 3 (real in Phase 6): the IG/FB connector. What is **deterministic placeholder** in Phase 3 (real in Phase 5): the confidence computer (self-consistency) and the jury (cross-family) — Phase 3 uses the existing `autonomy/jury.py` stub + a placeholder confidence, behind the same seams so Phase 5 swaps them with **no topology change**.

The slice is the shared spine (systemdesign §2) specialized for posting:

```
Research ─▶ Ideate ─▶ SelectAngle ─▶ Draft ─▶ Check&Score ─▶ Route ─┬─ auto ──▶ Publish(mock) ─▶ Done
 (RSCH-01)  (cell)    (pure code)   (cell)   (validators+flagger+    │
            idea       angle         draft    jury+confidence)        ├─ review ──▶ interrupt() + persist Action (PENDING)
                                                                      └─ regen ───▶ Draft   (bounded, recovery.py)
```

"idea → angle → draft" = **Ideate cell** (idea/angle candidates) → **SelectAngle** (pure-code pick) → **Copywriter cell** (the draft). Keeping angle *selection* in code honors the harness law (the model proposes; code decides). The existing `content_brief` cell is the seed for the Ideate cell's schema (it already carries `angle`/`caption`/guardrails); pm decides whether to extend it or split — the target interfaces below are normative, the refactor path is a grooming detail.

---

## Decision 1 — Cell contracts (typed I/O, every cell on `cells/base.py:Cell`)

Every LLM step is a `Cell[TOut]` (temp-0, pinned model, validator bank, typed-or-raise — `cells/base.py`). Concrete schemas:

```python
# --- Research (RSCH-01): NOT an LLM cell — a pluggable tool behind a budget ---
class Finding(BaseModel):       # one grounded research result
    source: str                 # "firecrawl" | "exa" | "meta_ad_library"
    url: str | None
    snippet: str
    score: float                # source-provided relevance

# --- Ideate cell: research -> angle candidates (composes brand-voice skill) ---
class Angle(BaseModel):
    hook: str                   # the one-sentence strategic angle
    rationale: str              # why it fits this tenant + the findings
    format_hint: MediaKind      # REEL | IMAGE | CAROUSEL  (informs media spec)
class AngleSet(BaseModel):
    angles: list[Angle]         # N candidates; SelectAngle picks one

# --- Copywriter cell: selected angle + grounding -> the post draft ------------
class MediaKind(str, Enum): REEL="reel"; IMAGE="image"; CAROUSEL="carousel"
class MediaSpec(BaseModel):
    kind: MediaKind
    aspect_ratio: str           # "9:16" | "1:1" | "4:5"
    duration_s: float | None    # reels only
class PostDraft(BaseModel):
    platform: Platform          # instagram | facebook  (cells/content_brief.Platform)
    caption: str
    hashtags: list[str]
    call_to_action: str
    media: MediaSpec

# --- AI-flagger cell: INDEPENDENT authenticity + safety pass over the draft ---
class AuthenticityVerdict(BaseModel):
    authentic: bool             # false => an "ai_authenticity" gate FAILS
    ai_smell: float             # 0..1 (higher = more AI-sounding)
    reasons: list[str]
    safety: SafetyVerdict       # autonomy.decision.SafetyVerdict: pass|flag|veto
```

**Rationale.** Typed I/O keeps raw model text off the wire (HARN-02, §6.3) and makes each cell directly eval-able under the Phase-2 ADR (one Inspect `Task` per cell, gold `Engine=POSTING`). The AI-flagger is a **separate cell from the copywriter** — a writer must not grade its own authenticity; this is the independent-classifier principle (AUTON-04) applied early.

---

## Decision 2 — Skill composition (Tier-1 brand-voice + AI-flagger)

Skills are **Anthropic Agent Skills** (stack-decision): folders of instructions/examples/scripts, loaded on demand. **The engine loads them; it does not author them.** Contract:

```python
class Skill(BaseModel):
    ref: str                    # "brand-voice/ink-studio", "ai-flagger"
    instructions: str           # composed into the cell's `instructions=` param
    examples: list[dict] = []   # optional few-shot exemplars

class SkillLoader(Protocol):
    def load(self, ref: str) -> Skill: ...   # on-demand, cached

# Cells are PARAMETERIZED by skills (the Cell framework already takes instructions):
def build_ideate_cell(voice: Skill, **o) -> Cell[AngleSet]: ...
def build_copywriter_cell(voice: Skill, exemplars: list[Exemplar], **o) -> Cell[PostDraft]: ...
def build_ai_flagger_cell(flagger: Skill, **o) -> Cell[AuthenticityVerdict]: ...
```

- **Brand-voice skill** ref comes from the tenant pack: `pack.voice.skill` (`config/schema.py:VoiceRef`, e.g. `brand-voice/ink-studio` in the seed pack). Per-tenant, loaded at run start.
- **AI-flagger skill** is a **global/system** skill (`ai-flagger`), not per-tenant — the same authenticity bar applies to every tenant.
- Composition = the skill's `instructions` are prepended to the cell's instruction string; `examples` augment the prompt. No new model machinery — it rides the existing `Cell(instructions=...)` seam.

**Rationale.** One loader, skills stay external + versioned (and their version flows into the Phase-2 `prompt_version`/eval identity), and per-tenant voice vs global authenticity is expressed by *where the ref comes from*, not by branching code.

---

## Decision 3 — Brand-voice grounding from the KB (KNOW-02)

The Copywriter cell is grounded on the tenant's past on-voice content via a new `KbStore` retrieval method (the store already has the gold/metric side from the Phase-2 ADR):

```python
class Exemplar(BaseModel):
    content: str; metrics: dict; similarity: float
# add to kb/store.py:
def voice_exemplars(self, *, tenant_id: str, query: str, k: int = 5) -> list[Exemplar]: ...
#   pgvector similarity over kb_chunks (kind in {"post","voice"}), tenant-scoped.
```

The Copywriter prompt is assembled in code from: **selected angle + research findings + brand-voice skill instructions + top-k voice exemplars**. Grounding is retrieval, not fine-tuning (VOICE-01 LoRA is deferred v2).

**Rationale.** Reuses the KNOW-01 pgvector KB and its tenant isolation; the `brand_voice_onvoice` metric already recorded there (`kb/store.py`) closes the loop — Phase-3 grounding feeds the Phase-2 eval the holdout measures.

---

## Decision 4 — Check & Score, route, and the channel/policy contract

`Check&Score` is a **pure-code node** that assembles the routing signals; it calls **no new router**:

1. **Deterministic gates** (POST-02) from an extended `ValidatorBank` — media/format + content gates → `list[Gate]`:
   - Reels **9:16, 5–90s**; image aspect/size; caption length; hashtag count/limits; banned phrases; pricing/claim checks; placeholder check; voice-similarity (vs KB).
2. **AI-flagger** → an `ai_authenticity` `Gate` (fails if `authentic=False`) **and** its `safety: SafetyVerdict` feeds the decision.
3. **Confidence** — Phase-3 deterministic placeholder (as `nodes.py:_confidence_for` does today); Phase-5 swaps in the self-consistency computer behind the same `state.confidence` field.
4. **Decision record** via the existing `autonomy/produce.py:produce_and_record_decision(...)` → stub jury (`autonomy/jury.py`) + `derive_decision` → persisted `DecisionRecord` (the console jury card binds to it). Phase-5 swaps the stub for the real jury — **no schema change**.

**Routing reuses the just-merged 2kp contract** (`phase1_slice.py`): `slice_route(pack, channel, confidence, gates)` → `resolve_channel_policy(pack, channel)` → `route(...)`. **`pack.autonomy_for(channel)` (mode + threshold) is the source of truth** — never a caller default. The posting engine acts on `config.Channel.INSTAGRAM` / `FACEBOOK`; the outbox uses `_SIDE_EFFECT_CHANNEL[channel] == Channel.POSTING`.

> **Honor `pack.is_enabled(channel)` (addresses a finding from the 2kp review):** the posting engine **produces nothing** for an `OFF`/disabled channel — it short-circuits before Ideate, rather than running the pipeline and queuing a REVIEW action for a channel the operator turned off. (`OFF → REVIEW` keeps the no-auto-fire safety property even if this is missed, but the engine should not do work for a disabled channel — the `config.schema` OFF contract is "produces nothing".)

**Rationale.** No second routing path can drift from the safety-reviewed one; the dial is the single source of truth; the autonomy record + jury + safety seams already exist and are swappable for Phase 5.

---

## Decision 5 — Publish via the mock connector (exactly-once boundary unchanged)

`auto` → enqueue through the **unchanged** side-effect boundary; `review` → LangGraph `interrupt()` + persist the `Action` (PENDING) for the console:

```python
class PublishIntent(BaseModel):     # the outbox payload
    platform: Platform; caption: str; hashtags: list[str]
    media_ref: str | None; scheduled_at: datetime | None
class PostingConnector(Protocol):   # dispatcher Connector shape
    async def publish(self, intent: PublishIntent) -> ProviderResult: ...   # {provider_id}
class MockPostingConnector:         # Phase-3: deterministic provider_id, records calls
    ...
```

Enqueue: `SideEffectBoundary.enqueue(conn, idempotency_key(tenant, Channel.POSTING, target, content), Channel.POSTING, payload)` inside the state-advancing tx; `Dispatcher` drains it through the `MockPostingConnector`. The same content derives the same key, so a replay never double-posts (HARN-04, §3) — proven by the existing exactly-once test pattern, now exercised end-to-end on the posting payload.

**Rationale.** Phase 3 changes *what* is produced, not *how it is committed*; reusing the boundary means the real Meta MCP in Phase 6 drops in behind `PostingConnector` with the slice unchanged.

---

## Decision 6 — Slice state

Extend `GraphState` (or a posting subgraph state) with typed posting channels (last-value for routing signals, append for the trajectory):

```python
angles: AngleSet | None
selected_angle: Angle | None
draft: PostDraft | None
authenticity: AuthenticityVerdict | None
decision_id: str | None          # FK to the persisted DecisionRecord
# reuse existing: confidence, gates (last-value), jury (last-value), step_log (append)
```

**Rationale.** Mirrors the Phase-1 `research`/`assembled` typing so the harness composes posting nodes without special-casing; every inter-node value stays typed.

---

## Build split (for pm → beads) and order

Parallelizable streams, each with the interface it owns. A–G can proceed concurrently; H integrates.

| Stream | Owns | Interface | Req | Depends |
|--------|------|-----------|-----|---------|
| **A** | Research adapter + budget cap | `ResearchSource.search(query, budget) -> list[Finding]`; pluggable (Firecrawl/Exa), one failing source never blocks | RSCH-01 | — |
| **B** | KB voice-exemplar retrieval | `KbStore.voice_exemplars(tenant_id, query, k)` | KNOW-02 | KB (exists) |
| **C** | Skill loader + composition | `SkillLoader.load(ref) -> Skill` | (enabler) | — |
| **D** | Ideate cell + SelectAngle (pure code) | `build_ideate_cell`, `select_angle(AngleSet, kb_history) -> Angle` | POST-01 | C |
| **E** | Copywriter cell | `build_copywriter_cell` | POST-01, KNOW-02 | B, C |
| **F** | Media/format validator bank | new `ValidatorBank` gates | POST-02 | validators (exists) |
| **G** | AI-flagger cell | `build_ai_flagger_cell` | (safety) | C |
| **H** | Posting subgraph wiring: Check&Score + route + mock publish + persist Action + `is_enabled` short-circuit | the spine; reuses `slice_route`, `produce_and_record_decision`, `SideEffectBoundary`, `MockPostingConnector` | POST-01(4) | all |

**Eval (cross-cutting, per Phase-2 ADR):** D/E/G each ship an Inspect `Task` + gold `Engine=POSTING` examples; F/router are pure-code per-commit gates. The slice is "done" when criterion (4) holds: a posting `Action` persists with a `DecisionRecord` confidence, visible to the (Phase-4) console.

---

## Edge cases

- **Disabled/OFF channel** → produce nothing (Decision 4); never run the pipeline or queue an action.
- **Out-of-spec media** → an `F` gate fails → `regenerate` (bounded via `harness/recovery.py`), not a human escalation.
- **Research budget exhausted / a source down** → degrade gracefully (pluggable adapter); the run continues on remaining findings rather than failing.
- **AI-flagger veto** (`safety=veto`) → never auto-fire regardless of confidence (independent safety veto, AUTON-04).
- **Regenerate loop** → bounded (retry → regenerate → human-review); after the budget, escalate to review, never loop.
- **Replay / crash** → the mock connector + idempotency key guarantee exactly-once (Decision 5); a re-run produces no second post.
- **Empty/short KB** (new tenant, few exemplars) → grounding degrades to skill-instructions-only; flag lower confidence rather than fabricate voice.

## Consequences

- pm can groom A–H into beads immediately; each has a named interface and a requirement.
- The posting slice is the **template** for outreach/engagement (Phase 7): same spine, swap cells + the `_SIDE_EFFECT_CHANNEL` target (gmail→OUTREACH already mapped).
- Phase-5 (real jury + self-consistency confidence) and Phase-6 (real Meta MCP) drop into the `produce_and_record_decision` and `PostingConnector` seams **without topology change**.
- Reuses the safety-reviewed 2kp routing as the single routing path — the content engine cannot introduce a second, unreviewed auto-fire path.

## References

- `engine/cells/base.py`, `cells/content_brief.py`, `cells/validators.py`; `engine/autonomy/{produce,jury,decision}.py`; `engine/harness/{router,state,recovery}.py`; `engine/kb/store.py`; `engine/sideeffects/{boundary,keys,dispatcher}.py`; `engine/config/schema.py`; `engine/phase1_slice.py` (2kp `slice_route` / `_SIDE_EFFECT_CHANNEL`)
- `docs/systemdesign.md` §2/§3/§6, `docs/stack-decision.md`, `docs/spec.md` §3/§5, `docs/adr/phase-2-eval-spine.md`
