# a9m.5 Draft (Create) cell — prompt/template design (build-ready)

**Author:** writer · **For:** super (hand-off) + the eng who builds a9m.5 · **Status:**
DESIGN — schemas now FINAL (arch a9m.1 **PR #38** + pmm contract). a9m.5 is mechanical
once a9m.1 lands and a9m.3 (`VoiceGrounding` impl) is built. **Aligned with:** arch
(MediaSpec/PostDraft, PR #38) + pmm (`positioning/voice-grounding-contract.md`).

## What the Draft cell is

The node `draft` in the Phase-3 graph (`… → angle → draft → media/format-validate →
score → route …`, a9m.1). It turns the selected **Angle** (a9m.4) + **VoiceGrounding**
(a9m.3) into a typed, on-voice **PostDraft** — no raw model text downstream. It is the
**copywriter skill (1mk.5) applied to the posting PostDraft**, grounded by brand-voice
(a9m.3 / 1mk.2) and gated by the **AI-flagger ruleset (AF-01..08, 1mk.3 / PR #48)**.

```
Angle (a9m.4) ─┐
               ├─► [draft Cell]  ──►  PostDraft{platform, caption, hashtags[], call_to_action, media}
VoiceGrounding ┘     (pinned Opus 4.8, temp 0; validator bank in-loop)
(a9m.3)
```

## Schemas — FINAL (consume, don't redefine)

**Output — arch a9m.1 Decision 1 (PR #38):**

```python
class MediaKind(str, Enum):
    IMAGE = "image"; REEL = "reel"; CAROUSEL = "carousel"; TEXT = "text"

class MediaSpec(BaseModel):
    kind: MediaKind
    aspect_ratio: str | None = None   # None for text; e.g. "4:5", "9:16"
    duration_s: float | None = None   # REEL only
    brief: str                        # what the creative should show (mock: no real asset)

class PostDraft(BaseModel):
    platform: Platform
    caption: str
    hashtags: list[str]               # without '#'
    call_to_action: str
    media: MediaSpec
```

**Grounding input — pmm contract §1 (arch envelope, PR #38 FINAL):**

```python
class Exemplar(BaseModel):
    content: str
    metrics: dict                     # e.g. {"on_voice": True, "engagement": ...}
    similarity: float                 # 1 - cosine; higher = closer

class VoiceDimensions(BaseModel):
    tone: list[str]                   # register, POV, CTA stance
    vocabulary: dict                  # {prefer, ban, approved_claims, emoji_policy, hashtag_policy}
    structure: list[str]              # rhythm, opener moves, emoji/hashtag density

class VoiceGrounding(BaseModel):
    tenant_id: str
    dimensions: VoiceDimensions       # ALWAYS present (from pack.voice.skill)
    exemplars: list[Exemplar]         # top-k from KB; [] if none
    coverage: Literal["FULL", "PARTIAL", "SPARSE"]
    low_grounding: bool               # coverage == SPARSE
    exemplar_count: int
```

**Angle** (a9m.4) — `{headline, angle, pillar, evidence/score, platform}` (consumed).
The verbatim dimension fills to template against:
`positioning/ladies8391/voice-dimensions.json` + `positioning/ink-studio/voice-dimensions.json`.

## Prompt / instruction template

Composition order is the contract — **grounding before task** (same principle as the
brand-voice + copywriter skills). Built per-run from `VoiceGrounding` + `Angle`.

```
# BRAND VOICE (source of truth — write as THIS artist, not generic AI)
## tone:        <VoiceGrounding.dimensions.tone[…]>
## structure:   <VoiceGrounding.dimensions.structure[…]>
## prefer:      <dimensions.vocabulary.prefer[…]>
## NEVER (hard ban): <dimensions.vocabulary.ban[…]>            # absolute — beats everything
## emoji:       <dimensions.vocabulary.emoji_policy>
## hashtags:    <dimensions.vocabulary.hashtag_policy>
## On-voice examples (mirror the rhythm; NEVER copy):
- <exemplars[i].content for top-k>                            # [] when SPARSE

# APPROVED CLAIMS (the ONLY claims you may state; a missing one is a blocker, not a gap)
- <dimensions.vocabulary.approved_claims[…]>

# TASK
You are the artist's content creator. Turn the WINNING ANGLE into ONE complete,
on-voice post draft: caption, hashtags, a call-to-action, and a media spec.

WINNING ANGLE:  headline / angle / pillar / platform  = <Angle.*>

Rules (copywriter recipe + posting):
- BRAND VOICE WINS over any pattern that fights it; only Approved claims.
- Caption: concrete, human, in the artist's voice; lead with the client/story or the
  work. Respect the platform caption limit. CTA on-voice (soft invite, not a hard close).
- Hashtags: follow the hashtag policy (count + lowercase + specific); no spam walls.
- media: choose kind (image | reel | carousel | text). reel ⇒ aspect_ratio "9:16" +
  duration_s in 5–90; image/carousel ⇒ aspect_ratio; text ⇒ no media. Always a one-line `brief`.
- NO AI tells (em-dash, contrast framing, rule-of-three, hedging, generic transitions,
  listicle/emoji-bullet — the AF ruleset). No placeholders.
- A needed claim not in Approved claims → do NOT write it.
```

Build function mirrors the existing cell pattern (`content_brief`, `copywriter`):

```python
def build_draft_cell(*, grounding: VoiceGrounding, platform: Platform,
                     config: FlaggerConfig = FlaggerConfig(), model=DRAFTING_MODEL,
                     **overrides) -> Cell[PostDraft]:
    return Cell(name="draft", schema=PostDraft, model=model,    # pinned Opus 4.8, temp 0
                instructions=build_draft_instructions(grounding, platform),
                validators=draft_validators(platform=platform, grounding=grounding, config=config),
                **overrides)
```

`build_draft_instructions()` reuses the brand-voice skill's prompt assembly +
the copywriter rule block; `approved_claims`, `ban`, `emoji/hashtag_policy` come from
`grounding.dimensions.vocabulary`.

## Validator bank (runs IN the cell repair loop; ERROR → repair/regenerate)

| Check | Source | Severity |
|---|---|---|
| caption non-empty / no placeholder | `non_empty`, `no_placeholder` | ERROR |
| caption length (platform) | char cap (IG 2200) | ERROR |
| **AI tells over caption + CTA** (AF-01..08) | `ai_flagger(..., config)` (1mk.3) | ERROR (triad WARN) |
| banned slop | `ai_flagger` BANNED_SLOP (eng3 #57) | ERROR |
| **voice ban-lexicon** | `ban_lexicon(dimensions.vocabulary.ban)` — hard, beats everything | ERROR |
| hashtags policy | `hashtags_policy(dimensions.vocabulary.hashtag_policy)` (count/lowercase/banned) | ERROR/WARN |
| **claim discipline** | `claims_in_approved_set(dimensions.vocabulary.approved_claims)` | ERROR (see §claim-gate) |
| **voice-similarity** | `voice_similarity(grounding.exemplars)` via `Exemplar.similarity`; lexical proxy when SPARSE | WARN→ERROR (knob) |
| media coherence | `media_valid()` — reel ⇒ 9:16 + 5–90s; image/carousel ⇒ aspect_ratio; text ⇒ none | ERROR |

- **Claim-gate disposition (pmm §3.1):** Phase-3 = **regenerate-then-escalate** for all
  claim violations (safe — the global autonomy hold routes all output to review anyway).
  The **escalate-immediately** subset (`sensitive_ban` via
  `positioning/sensitive-ban-patterns.json`) is a **separate, independent
  gate-disposition bead — NOT CustomerAcq-439** (439 is the *global autonomy hold*, a
  different thing; the gate-disposition matters both under it and after it lifts). pmm
  owns the pattern content; eng3/qa own the gate wiring. **a9m.5 does not wire it** —
  Phase-3 stays regenerate-then-escalate.
- **Persistently off-voice/banned** → `CellError` after the retry budget → review;
  banned draft never emitted (a9m.5 AC). Over-length / hashtag-wall → repaired or failed,
  never silently truncated.
- `media_valid()` produces a *coherent* spec; **a9m.6 (POST-02)** owns the full
  per-kind gate (REEL 9:16 + 5–90s; IMAGE/CAROUSEL aspect/size; TEXT no media gate).

## Degrade ladder (pmm §3) — coverage drives confidence, never silence

| KB state | `coverage` | `low_grounding` | Draft behavior |
|---|---|---|---|
| enough tenant on-voice posts | `FULL` | `False` | top-k exemplars + dimensions |
| thin past content | `PARTIAL` | `False` | available exemplars + dimensions (leans on dimensions) |
| empty / unreachable / new tenant | `SPARSE` | `True` | **dimensions-only** (skill always resolves) |

`dimensions` are ALWAYS present (from `pack.voice.skill`), so the draft is always
grounded. `low_grounding=True` is a **signal, not a failure**: the draft still runs on
dimensions; **Check&Score (a9m.7) lowers confidence** → 439-held routing sends it to
**review**, never auto. **Never silently emit generic copy** (the KNOW-02 failure).
Confidence lives on the score/Action, not on `PostDraft` (arch schema has no
confidence field).

## How the three skills compose

| Skill | Role |
|---|---|
| **brand-voice grounding (a9m.3 / 1mk.2)** | `VoiceGrounding.dimensions` (from `pack.voice.skill`) injected first; exemplars from the KB. Engine loads the **skill**, not `brand-dna.md` directly. |
| **copywriter (1mk.5)** | The rule block IS the copywriter recipe, specialized from `CopywriterDrafts` variants to one `PostDraft`. |
| **AI-flagger (AF-01..08)** | `ai_flagger` over caption + CTA — the HARD no-AI-tells gate, same detector everywhere. |

## Edge-case → behavior (from the AC + pmm contract)

| Edge case | Behavior |
|---|---|
| Persistent off-voice/banned | `CellError` after retries → review; never emit raw/banned. |
| Over-length caption / hashtag-wall | validator ERROR → repair or fail; no silent truncation. |
| reel / image / carousel / text | `media.kind` + `media_valid()`; a9m.6 does the full per-kind gate. |
| SPARSE grounding (empty KB / new tenant) | dimensions-only; `low_grounding` → Check&Score lowers confidence → review. |
| claim not in approved set | Phase-3: regenerate-then-escalate. (sensitive-ban escalate-immediately = separate gate-disposition bead, NOT 439, not wired here.) |
| No viable angle upstream | the `angle` node's empty/abort path (a9m.1); draft not entered. |
| PII / secrets | pack uses `SecretRef`; never echo input verbatim. |

## Persistence + autonomy

- Validated `PostDraft` → `GraphState` (artifact) for the scorer (a9m.7) + console;
  `first_pass_valid` / `repairs` via `cell.metrics` (validator-rate). Routing is
  **autonomy-HELD (439)** → review/regenerate only; the Draft cell never auto-publishes.

## Test plan (FunctionModel injection — no API key, like HARN-02 / copywriter)

1. Angle + VoiceGrounding → typed `PostDraft` (caption + hashtags + CTA + media).
2. Banned-phrase / AI-tell injected → repaired then accepted; persistent → `CellError`.
3. Over-length caption / hashtag-wall → flagged.
4. reel angle → `media.kind==reel`, aspect_ratio "9:16", duration_s in 5–90;
   image/carousel → aspect_ratio, no duration; text → no media.
5. SPARSE grounding → dimensions-only draft + `low_grounding` surfaced (no silent generic).
6. claim not in `approved_claims` → regenerate-then-escalate.
7. PostDraft persists in `GraphState`; first-pass + after-retry validity reported.

## Build-ready checklist (a9m.5, once a9m.1 lands + a9m.3 built)

- [ ] Import `MediaSpec`/`PostDraft` (arch PR #38) + `VoiceGrounding` (a9m.3).
- [ ] `build_draft_instructions()` — brand-voice prompt assembly + copywriter rule block.
- [ ] `draft_validators()` — `ai_flagger`, length, `ban_lexicon`, `hashtags_policy`,
      `claims_in_approved_set` (regenerate-then-escalate), `voice_similarity`, `media_valid`.
- [ ] `build_draft_cell()` — pinned Opus 4.8, temp 0.
- [ ] Persist PostDraft → GraphState; wire metrics.
- [ ] `tests/test_draft_cell.py` — the 7 cases above (FunctionModel).

## Dependency for a9m.3 — the skill emits the dimensions (writer-owned, pmm §2)

`VoiceGrounding.dimensions` are **emitted by the brand-voice skill (1mk.2) from
`brand-dna.md`** — the engine resolves `pack.voice.skill`, loads the skill, and the
skill surfaces `VoiceDimensions`. **writer wires this emission**; pmm owns which DNA
section fills which field. Reference fills (verbatim) live in
`positioning/<tenant>/voice-dimensions.json`. Tracked as a writer follow-up
(extend the brand-voice resolver to emit `VoiceDimensions`, verified == the reference
fills) so a9m.3's engine half has a ready dimensions source at build time.

## Alignment status

- **arch (a9m.1 PR #38):** MediaSpec/PostDraft FINAL and adopted here (incl. `carousel`,
  `aspect_ratio` rename, `brief`). Topology + autonomy-HELD routing per the ADR.
- **pmm (`voice-grounding-contract.md`):** VoiceGrounding §1 shape, §3 degrade ladder,
  §3.1 claim-gate disposition adopted verbatim; dimension fills from
  `voice-dimensions.json`.
- **eng3 (#57):** once BANNED_SLOP is in `detect_ai_tells`, `ai_flagger` over the caption
  covers slop — single slop source (no separate `banned_phrases` over the caption).
