# Style-preference memory — wiring notes (trainable-drafts loop)

**Source:** client direction, PA meeting 2026-07-11. Jigger called the drafts
"generic" and framed the system as "a trainable agent … we can start training it."

**STATUS: SHIPPED.** The loop is closed end-to-end and live-verified: the Review
Queue's real `editActionDraft` GraphQL mutation captures each (original, edited)
pair (`obsapi.repo.edit_action_draft` → `studio.style_memory.record_style_edit`),
persists it on the `memories` table's `style` subject (CHECK widened idempotently,
idempotent per exact edit pair), and the accumulated preferences feed back into
BOTH drafting surfaces — the IG brief (`build_ig_brief_block`) and every outreach
draft (`resolve_brand_voice` appends the learned block). A `sent` action's draft
is the delivery audit record and now refuses edits. The sections below stand as
the original design record.

## What is DONE (the trainable core — `engine/studio/style_memory.py`)

Pure, deterministic, unit-tested (`engine/tests/test_style_memory.py`):

- `learn_style_preference(original, edited)` — distills ONE edit into deterministic
  signals: `shorter`, `no_emoji`, `less_hype`, `drop_discounts`, `fewer_hashtags`,
  plus the verbatim phrases the operator removed. Nothing is invented — every
  signal traces to a real change; an unchanged draft yields nothing.
- `accumulate_preferences(edits)` — merges many edits; a signal becomes a firm
  `RULE` only after it recurs (threshold 2), otherwise it stays a softer
  `suggestion`. Removed phrases become `avoid_phrases` only on repetition.
- `render_style_preferences_block(prefs)` — renders the brief block that ORDERS the
  next draft to honor the learned rules; empty in → empty out (no block).

## What REMAINS (two wiring steps)

### 1. Draft-edit-capture hook (the missing trigger)

There is no backend endpoint today that receives an operator's **edited draft
body**. The approve (`/studio/campaign/action/{id}/schedule`) and override
(`/studio/campaign/action/{id}/override`) routes carry a `reason` / `live` flag,
not revised caption text, so no `(original, edited)` pair is ever produced — which
is why nothing feeds the core above yet.

Proposed contract (additive, does not change the locked design's look): when the
operator edits-then-approves a draft, the console POSTs the pair, e.g.

```
POST /studio/style/learn
{ "actionId": "act_…", "original": "<the drafted caption>", "edited": "<operator text>" }
```

The handler calls `learn_style_preference(original, edited)` and, when it yields
signals, persists them (step 2). No send, no side effect — a pure learning write.

### 2. Persistence + brief read-back

- **Persist** each captured edit as a `style` subject on the `memories` table.
  Its `subject_type` CHECK is currently `('customer','campaign','conversation',
  'fact')` (+ `'artist'` added by `studio.artist_memory.ensure_artist_memory_schema`);
  widen it to include `'style'` the same idempotent way. Store `metadata =
  {original, edited, signals, removed_phrases}` so the accumulator can be rebuilt
  deterministically; embedding may be NULL (no semantic recall needed).
- **Read back** in `studio.ig_pipeline.build_ig_brief_block`: load the tenant's
  style edits, run `accumulate_preferences`, and append
  `render_style_preferences_block(prefs)` right after the brand-study block. Empty
  history → empty block, so this is safe to wire the moment capture exists.

## Why the read side is deferred, not shipped now

Wiring the brief read-back **before** the capture hook exists would append an
always-empty block on every draft — dead weight, not a feature. So the honest
sequencing is: land the capture hook (step 1) → persistence + read (step 2)
together, so the loop is functional and end-to-end verifiable from its first use.
The whole loop is backend-testable against the `memories` table once the endpoint
lands; nothing here depends on an external vendor (unlike the Meta Pixel work).
