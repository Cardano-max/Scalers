# a9m.5 Draft (Create) cell — prompt/template design (build-ready)

**Author:** writer · **For:** super (hand-off) + the eng who builds a9m.5 · **Status:**
DESIGN ONLY — a9m.5 is dep-blocked on **a9m.1** (ADR) + **a9m.3** (VoiceGrounding).
This makes a9m.5 mechanical once those land. **Align:** arch (a9m.1 schemas/topology)
+ pmm (a9m.3 voice dimensions). Nothing here is implemented; it is the cell's
prompt/template + schema + validator + test contract.

## What the Draft cell is

The node `draft` in the Phase-3 graph (`… → angle → draft → media/format-validate →
score → route …`, a9m.1). It turns the selected **Angle** (a9m.4) + **VoiceGrounding**
(a9m.3) into a typed, on-voice **PostDraft{caption, hashtags[], media_spec}** — with
no raw model text downstream. It is, in effect, the **copywriter skill (1mk.5)
applied to the posting PostDraft**, grounded by brand-voice (a9m.3 / 1mk.2) and gated
by the **AI-flagger ruleset (AF-01..08, 1mk.3 / PR #48)**.

```
Angle (a9m.4) ─┐
               ├─► [draft Cell]  ──►  PostDraft{caption, hashtags[], media_spec}
VoiceGrounding ┘     (pinned Opus 4.8, temp 0; validator bank in-loop)
(a9m.3)
```

## Schemas (proposed — to be ratified by the a9m.1 ADR)

```python
class MediaKind(str, Enum):
    IMAGE = "image"; REEL = "reel"; TEXT = "text"   # text-only = no media

class MediaSpec(BaseModel):
    kind: MediaKind
    ratio: str | None = None        # e.g. "4:5", "9:16"; None for text-only
    duration_s: int | None = None   # REEL only; None otherwise
    brief: str = ""                 # what the creative should show (for the human/POST-02)

class PostDraft(BaseModel):
    caption: str
    hashtags: list[str] = Field(default_factory=list)   # without '#'
    media_spec: MediaSpec
    pillar: str = ""                # which brand pillar this ladders to (traceability)
    grounding_confidence: float = Field(ge=0.0, le=1.0, default=1.0)  # from VoiceGrounding
```

**Consumed inputs** (shapes owned by a9m.4 / a9m.3 — referenced, not redefined):
- `Angle` — `{headline, angle, pillar, evidence/score, platform}` (a9m.4).
- `VoiceGrounding` (a9m.3) — `{exemplars: list[str], dimensions: {tone, vocabulary,
  structure}, coverage: float, low_grounding: bool}`, tenant-scoped, from the pack
  voice ref (always) + pgvector KB (when seeded). The draft cell **consumes**, never
  re-queries.

> Open items for a9m.1/a9m.3 to confirm: exact `Angle`/`VoiceGrounding` field names;
> whether `grounding_confidence` is sourced from `VoiceGrounding.coverage` (proposed:
> yes). Everything else below is invariant to those names.

## Prompt / instruction template

Composition order is the contract — **grounding before task**, so the cell reads the
artist's voice before it writes (same principle as the brand-voice + copywriter
skills). Built per-run from VoiceGrounding + Angle; the static rule block is the
copywriter recipe specialized to a PostDraft.

```
# BRAND VOICE (source of truth — write as THIS artist, not generic AI)
<VoiceGrounding.dimensions: tone / vocabulary / structure>
## On-voice examples (mirror the rhythm; never copy):
- <VoiceGrounding.exemplars[0..N]>

# APPROVED CLAIMS (the ONLY claims you may state; a missing one is a blocker, not a gap)
- <approved_claims from the pack>

# TASK
You are the artist's content creator. Turn the WINNING ANGLE below into ONE
complete, on-voice post draft: a caption, hashtags, and a media spec.

WINNING ANGLE:
  headline: <Angle.headline>
  angle:    <Angle.angle>
  pillar:   <Angle.pillar>
  platform: <Angle.platform>

Rules (copywriter recipe + posting):
- BRAND VOICE WINS over any pattern that fights it; only Approved claims.
- Caption: concrete and human, in the artist's voice; lead with the client/story or
  the work, not the studio. Respect the platform caption limit.
- Hashtags: follow the pack's hashtag policy (count + lowercase + specific tags);
  no spam walls, no banned tags.
- media_spec: choose kind (image | reel | text). For reel, set ratio (9:16) and a
  duration; for image, set ratio (e.g. 4:5); write a one-line creative `brief`.
- NO AI tells (em-dash, contrast framing, rule-of-three, generic transitions,
  hedging, listicle/emoji-bullet — the AF ruleset). No placeholders.
- If the angle needs a claim that is not approved, do NOT write it.
```

The build function mirrors the existing cell pattern (`content_brief`, `copywriter`):

```python
def build_draft_cell(*, grounding: VoiceGrounding, approved_claims: tuple[str, ...] = (),
                     platform: Platform, config: FlaggerConfig = FlaggerConfig(),
                     model=DRAFTING_MODEL, **overrides) -> Cell[PostDraft]:
    return Cell(name="draft", schema=PostDraft, model=model,   # pinned Opus 4.8, temp 0
                instructions=build_draft_instructions(grounding, approved_claims, platform),
                validators=draft_validators(platform=platform, grounding=grounding, config=config),
                **overrides)
```

`build_draft_instructions(...)` assembles the template above from the grounding +
approved claims (reuse the brand-voice resolver `system_prompt` assembly from
`skills/brand-voice/verify/resolve_brand_voice.py` and the copywriter rule block).

## Validator bank (runs IN the cell repair loop; ERROR → repair/regenerate)

Reuse the existing deterministic validators; the Draft cell is a thin specialization.

| Check | Source | Severity |
|---|---|---|
| caption non-empty / no placeholder | `non_empty`, `no_placeholder` | ERROR |
| caption length (platform) | `word_count_between` / char cap (IG 2200) | ERROR |
| **AI tells over caption** (AF-01..08) | `ai_flagger("caption", config)` (1mk.3) | ERROR (triad WARN) |
| banned slop | `ai_flagger` BANNED_SLOP (post eng3 #57) or `banned_phrases` | ERROR |
| hashtags count + style | new `hashtags_policy(pack)` (count, lowercase, banned tags) | ERROR/WARN |
| **claim discipline** | new `claims_in_approved_set(approved_claims)` — flags claim-like sentences not covered | ERROR → escalate |
| **voice-similarity** | `voice_similarity(grounding.exemplars)` — lexical proxy now; embedding/KB cosine when a9m.3 KB seeded | WARN→ERROR (knob) |
| media_spec coherence | new `media_spec_valid()` — reel ⇒ ratio+duration; image ⇒ ratio; text ⇒ neither | ERROR |

- **Persistently off-voice / banned** → after the retry budget the cell raises
  `CellError` on a code path (HARN-02) → route to review; the banned draft is never
  emitted (matches a9m.5 AC).
- **Over-length / too many hashtags** → caught here, repaired or failed; never
  silently truncated downstream.

`draft_validators()` returns a `ValidatorBank` composed of the above, exactly like
`copywriter_validators()` / `reply_validators()`.

## How the three skills compose

| Skill | Role in the Draft cell |
|---|---|
| **brand-voice grounding (a9m.3 / 1mk.2)** | `VoiceGrounding` (exemplars + dimensions) injected **first** in the prompt; `grounding_confidence` carried onto the PostDraft. Low-grounding → lower confidence → review (never silent generic copy). |
| **copywriter (1mk.5)** | The rule block IS the copywriter recipe (mold the winning angle into the voice; voice beats pattern; approved claims only), specialized from `CopywriterDrafts` variants to one `PostDraft`. |
| **AI-flagger (AF-01..08, 1mk.3 / #48)** | `ai_flagger` over the caption in the bank — the HARD no-AI-tells gate; identical detector as every other writing cell. |

## Edge-case → behavior (from the AC)

| Edge case | Behavior |
|---|---|
| Persistent off-voice/banned | `CellError` after retries → review; never emit raw/banned. |
| Over-length caption / too many hashtags | validator ERROR → repair or fail; no silent truncation. |
| Reel vs image vs text-only | `media_spec.kind` + `media_spec_valid()`; reel carries ratio+duration for POST-02 (a9m.6). |
| Low-grounding angle (empty research/KB) | draft still produced from pack voice ref; `grounding_confidence` lowered, `low_grounding` surfaced → route to review. |
| No viable angle upstream | not this cell — the `angle` node's empty/abort path (a9m.1); draft is not entered. |
| PII / secret leakage | no secrets in prompt context (pack uses `SecretRef`); validator may screen obvious PII; never echo input verbatim. |

## Persistence + autonomy

- The validated `PostDraft` is written into `GraphState` (artifact) for the scorer
  (a9m.7) + the console; `first_pass_valid` / `repairs` recorded via `cell.metrics`
  (validator-rate metric, like every cell).
- Routing is **autonomy-HELD (439)**: `route()` yields only review/regenerate in this
  slice (per a9m.1) — the Draft cell never auto-publishes.

## Test plan (FunctionModel injection — no API key, like HARN-02 / copywriter)

1. Given an Angle + VoiceGrounding, the cell returns a typed `PostDraft`
   (caption + hashtags + media_spec).
2. Banned-phrase / AI-tell injected draft → repaired then accepted; persistent →
   `CellError` (never returned raw).
3. Over-length caption / hashtag-wall → flagged by the bank.
4. Reel angle → `media_spec.kind == reel` with ratio + duration; image angle →
   ratio, no duration; text-only → neither.
5. Low-grounding VoiceGrounding → `grounding_confidence` lowered + low-grounding
   flagged (no silent generic copy).
6. PostDraft persists in `GraphState`; first-pass + after-retry validity reported.
7. Two-tenant: grounding from tenant A never yields tenant-B voice (covered in
   a9m.3, re-asserted here).

## Build-ready checklist (a9m.5, once a9m.1 + a9m.3 land)

- [ ] Confirm `Angle` / `VoiceGrounding` field names against the a9m.1 ADR + a9m.3 payload.
- [ ] Add `MediaSpec` + `PostDraft` schemas (or import from the ADR's schema module).
- [ ] `build_draft_instructions()` — reuse brand-voice prompt assembly + copywriter rule block.
- [ ] `draft_validators()` — reuse `ai_flagger`, length, banned-slop; add
      `hashtags_policy`, `claims_in_approved_set`, `voice_similarity`, `media_spec_valid`.
- [ ] `build_draft_cell()` — pinned drafting model (Opus 4.8), temp 0.
- [ ] Persist PostDraft into GraphState; wire metrics.
- [ ] `tests/test_draft_cell.py` — the 7 cases above (FunctionModel).

## Notes for alignment

- **arch (a9m.1):** this assumes the ADR's `PostDraft{caption, hashtags, media_spec}`
  and the `… angle → draft → media-validate …` topology. If the ADR names the
  schemas/fields differently, only the schema imports change; the prompt/validator
  contract holds. Please confirm `MediaSpec` shape (kind/ratio/duration) is what
  POST-02 (a9m.6) will validate against.
- **pmm (a9m.3):** this consumes `VoiceGrounding{exemplars, dimensions{tone,
  vocabulary, structure}, coverage, low_grounding}`. Confirm the dimension names so
  the prompt template uses yours verbatim; confirm `grounding_confidence` ←
  `coverage`.
- **eng3 (#57):** once BANNED_SLOP is in `detect_ai_tells`, the Draft cell's
  `ai_flagger("caption")` covers slop too — use the detector as the single slop
  source (don't also add `banned_phrases` over the caption).
