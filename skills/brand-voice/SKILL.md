---
name: brand-voice
description: Use when any cell drafts, rewrites, or revises tenant-facing copy (captions, comment/DM replies, cold outreach, hooks/CTAs, ad copy) and the output must read as the specific artist's voice rather than generic AI. Loads that tenant's brand DNA (positioning, personas, messaging pillars, approved claims, do/do-not) and on-voice example captions before a single word is written. Trigger words: brand voice, on-voice, tone, positioning, persona, caption, copy, draft, rewrite, humanize, off-brand.
license: Apache-2.0 (derivative — see NOTICE)
upstream: anthropics/skills — skills/brand-guidelines (Apache-2.0)
pinned: b9e19e6f44773509fbdd7001d77ff41a49a486c1
---

# Brand Voice (per-artist)

## Overview

This skill makes a writing cell **start from the artist's actual voice, not a
generic SaaS voice.** It loads the tenant's *brand DNA* and a few *high-performing,
on-voice examples* into the cell's context before drafting, so the first draft is
already on-brand and the validator/jury/confidence gates have far less to repair.

**Core principle (from the practitioner KB):** prompt-context quality beats the
specific skill. The per-tenant pack and the artist's real data (the winning-angle
KB) matter more than any off-the-shelf prompt.

> "the skill loads company positioning, customer personas, messaging pillars,
> competitor context, approved claims, product details, and examples of
> high-performing content. It dramatically reduces the amount of editing required
> because Claude starts from the company's actual voice rather than a generic
> SaaS voice." — emilyxhug, r/AskMarketing (verbatim, winning-strategies-kb.md)

This is the **core skill**: everything that writes depends on it (copywriter,
reply, outreach all build on the brand context it assembles).

## When to use

Load this skill whenever a cell is about to produce or revise **tenant-facing
natural language**:

- Posting: captions, hooks, CTAs, hashtags
- Reply: comment and DM responses
- Outreach: cold-email copy
- Any rewrite/humanize pass on copy

**Do NOT** use it for non-language artifacts (scheduling math, routing decisions,
classification/extraction). It grounds *voice*; it does not make decisions.

Conditionally loaded **by state**: the harness loads it only on cells whose output
is tenant-facing copy, keyed off the run's cell type — it is not in context for
code/decision cells.

## How the skill resolves (loaded on demand by the engine)

The per-tenant pack (`engine/config/packs/<tenant>.toml`) names the voice in
`[voice]`:

```toml
[voice]
skill = "brand-voice/<tenant>"                       # this skill + that tenant's DNA
examples_uri = "minio://voice/<tenant>/examples.jsonl" # on-voice few-shot examples
```

The ref `brand-voice/<tenant>` resolves to **two things merged**:

1. **This shared skill** (`skills/brand-voice/SKILL.md`) — the technique below.
2. **That tenant's brand DNA** (`skills/brand-voice/tenants/<tenant>/brand-dna.md`)
   — the artist's actual positioning/personas/pillars/claims/do-donts/examples.

The engine assembles the cell's system context from (shared skill + tenant DNA +
N on-voice examples pulled from `examples_uri` / the KB). See
`verify/resolve_brand_voice.py` for the reference resolver and the exact assembly
order.

## Emitted VoiceDimensions (a9m.3 / KNOW-02)

The skill also **emits typed `VoiceDimensions`** (the machine-readable view of the
DNA) that the Phase-3 `build_voice_grounding` (a9m.3) and Draft cell (a9m.5)
consume — per `pmm`'s `positioning/voice-grounding-contract.md` §1/§2. Each tenant
bundle ships `tenants/<tenant>/voice-dimensions.json`:

```
dimensions = { tone: [...], structure: [...],
               vocabulary: { prefer, ban, approved_claims, emoji_policy, hashtag_policy } }
```

The resolver returns it on `BrandVoiceContext.dimensions`. The mapping from DNA
sections → dimensions is fixed (Voice & tone → `tone`/`structure`/emoji+hashtag
policy; Do → `prefer`; Do-not → `ban`; Approved claims → `approved_claims`), so at
onboarding you swap `brand-dna.md` + `voice-dimensions.json` together and the
dimensions regenerate with **zero rework**. The emission is verified == the
reference fill in `verify/demo_brand_grounding.py`.

## The brand-DNA contract

Every tenant DNA file (`tenants/<tenant>/brand-dna.md`, schema in
`references/brand-dna.template.md`) supplies these sections. The cell MUST treat
them as the source of truth and MUST NOT invent values not present here:

| Section | What it constrains | Failure if ignored |
|---|---|---|
| **Positioning** | Who the artist is, the one-line promise, what they are NOT | Generic, interchangeable copy |
| **Personas** | Who the copy speaks to (their words, fears, desires) | Talks past the actual audience |
| **Messaging pillars** | The 3–5 themes every post ladders back to | Off-strategy one-offs |
| **Approved claims** | The ONLY factual/credential claims allowed | Fabricated or unsafe claims |
| **Voice & tone rules** | Register, rhythm, person, emoji/hashtag policy | Wrong-sounding copy |
| **Do / Do-not** | Explicit lexicon and bans | AI tells, banned phrases, off-brand words |
| **On-voice examples** | Few-shot anchors of proven copy | Drifts from what actually performs |

## How to apply it (the writing recipe)

1. **Read the DNA first.** Before drafting, read positioning + the relevant
   pillar(s) + the persona you are addressing. State (internally) which pillar
   this piece ladders to.
2. **Mirror the examples, don't copy them.** Match the rhythm, sentence length,
   opener style, and emoji/hashtag density of the on-voice examples. Never reuse
   their exact sentences.
3. **Stay inside approved claims.** Any factual/credential/offer claim must appear
   in the DNA's approved-claims list. If the copy needs a claim that is not there,
   **do not write it** — see edge cases (block + escalate).
4. **Apply do/do-not as hard constraints.** The Do-not lexicon is a ban list; the
   Do lexicon is the preferred vocabulary. Honor both literally.
5. **Write for the persona, in the artist's register.** Concrete and human; no AI
   boilerplate, no placeholders.

The output still flows through the deterministic validator bank, the jury, and the
confidence/autonomy gate. This skill improves the *starting point*; it does not
replace any gate. (See the AI-flagger validator, bead 1mk.3.)

## Edge cases (required behavior)

| Situation | Required behavior |
|---|---|
| **New artist, little past content** | **Graceful degrade to positioning-only.** Write from positioning + pillars + voice rules; do not fabricate examples or personas. Lower confidence so the router queues for review. |
| **Conflicting do / do-not** (a word is in both, or a pillar contradicts a ban) | The **Do-not list wins** (bans are safety). Flag the conflict in the cell's notes so the operator can fix the DNA; do not silently pick. |
| **Claim not in the approved set** | **Block + escalate.** Do not emit the copy. Surface the missing claim to the operator (HITL) rather than guessing or softening it into an unverifiable statement. |
| **Multi-artist tenant** | The pack ref is per-artist (`brand-voice/<artist>`). Never blend two artists' DNA in one piece; load exactly the addressed artist's DNA. If the run does not name an artist, queue for review rather than defaulting. |

## Quick reference

- **Source of truth:** `tenants/<tenant>/brand-dna.md` — never override it from memory.
- **Examples:** mirror rhythm/structure; never copy sentences.
- **Claims:** allowed set only; missing claim → block + escalate.
- **Bans:** Do-not list is absolute and beats everything.
- **Sparse tenant:** positioning-only + lower confidence → review.
- **Still gated:** validators + jury + confidence run after; this is the starting point, not the gate.

## Common mistakes

- **Treating the DNA as optional flavor.** It is the source of truth — read it before writing, not after.
- **Copying example captions.** Few-shots are rhythm anchors, not a clipboard.
- **Inventing credentials/offers** to make a line land. Claims are allow-listed; a missing one is a blocker, not a creative gap.
- **Averaging the voice across artists** on a multi-artist tenant. Load one artist, write as one artist.

## Provenance

Structure adapted (Apache-2.0 derivative) from Anthropic's first-party
`brand-guidelines` skill — the conditionally-loaded "carry the brand's specific
assets + how to apply them" pattern — re-authored for brand *voice* and grounded
in `docs/skills/winning-strategies-kb.md`. Upstream pin and license terms are in
`NOTICE` and `registry-entry.md`.
