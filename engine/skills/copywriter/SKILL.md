---
name: copywriter
description: Use when the posting copywriter cell turns a scored winning angle into publish-ready social copy — hooks, captions, and CTAs for a tattoo artist. Molds proven content-repurposing / hook patterns into the specific artist's voice and emits several distinct on-brand variants. Trigger words: copywriter, hook, CTA, caption, variants, repurpose, winning angle, content patterns, scroll-stopper.
license: pattern-adoption (no upstream code vendored — see VETTING.md)
---

# Copywriter (hook / CTA)

## Overview

Takes a **scored winning angle** (the proven pattern/insight that research +
the strategist surfaced) and molds it into the **artist's voice** as several
distinct hook + caption + CTA drafts. The job is *re-expression*, not invention:
reuse what wins, in a voice that's unmistakably this artist's.

**Core principle (KB):** skills are step one; the winning pattern + the artist's
real voice are the edge. The copywriter is where a research-proven angle becomes
on-brand copy.

> "a content repurposing skill … automatically creates LinkedIn posts, email
> drafts, ad angles, FAQs … following predefined formats." — emilyxhug
> "Omnichannel Repurposing Chains: … running back-to-back specific prompts to
> break it down … a unified message across the entire ecosystem." — tenegoacademy
> (both verbatim, winning-strategies-kb.md)

## When to use

Load on the **copywriter cell** when producing organic post copy (hooks,
captions, CTAs) from an angle. Not for: picking the angle (that's the strategist),
research, replies, or outreach (their own skills).

Conditionally loaded by the harness on the copywriter cell only.

## Composition (this skill depends on two others — both now in-use)

| Dep | Role here | Where |
|---|---|---|
| **S2 brand-voice** (`1mk.2`) | Supplies the artist's positioning, pillars, approved claims, do/do-not, on-voice examples. The resolver assembles this context; it is injected into the cell instructions **before** the rules so the cell reads the voice before it writes. | `skills/brand-voice/` |
| **S3 AI-flagger** (`1mk.3`) | The deterministic `detect_ai_tells` detector runs over **every variant's** hook/caption/CTA as an ERROR validator. A draft with em-dash / contrast framing / rule-of-three / generic transition is repaired or fails on a code path. | `engine/cells/ai_flagger.py` |

After the bank passes, the harness jury + confidence/autonomy gate route the
output. **This skill never auto-ships** — clearing the gates is the harness's job
(autonomy hold, bead 439).

## The recipe

1. **Read the brand voice first** (S2 context). Note which pillar the angle serves.
2. **Pick patterns from the library** (`references/hook-cta-patterns.md`) that fit
   the angle — a different pattern per variant for variety.
3. **Mold, don't paste.** Express the winning pattern in the artist's rhythm and
   lexicon; mirror the on-voice examples without copying them.
4. **Stay inside approved claims.** A claim the angle needs but the DNA doesn't
   list → do not write it; flag for the operator (see edge cases).
5. **Produce 2–4 DISTINCT variants** — different hooks/openers, each laddering to a
   stated pillar, each with a soft on-voice CTA, each within platform length.

## Do / Do-not (from docs/skills/skills-dos-donts.md)

**Do:** start from the artist's actual voice, never generic; reuse research-proven
patterns; give variety (vary the hook, not just the words); keep CTAs soft and
human; lead with the client/story or the work.

**Do-not:** ship AI tells (em-dash, rule-of-three, contrast framing, generic
transitions) — the S3 flagger blocks them; over-template (N variants that are one
template refilled); invent claims to make a line land; let a clever pattern
override the brand voice; expect the first draft to be the last — it's gated.

## Edge cases (required behavior)

| Situation | Required behavior |
|---|---|
| **A pattern fights the brand voice** (or hits a do-not) | **Brand voice wins.** Drop or adapt the pattern; never bend the voice to fit a pattern. |
| **Over-templated output** | The `hooks_distinct` validator errors on duplicate hooks; vary structure across variants (variety guidance in the rules). |
| **Platform length limits** | `platform_length` caps caption chars per platform (IG 2200, FB 5000) and hook words (≤14); a soft word-count WARN keeps posts snappy. |
| **Angle needs an unapproved claim** | Do **not** write the claim; flag it for the operator (HITL), same rule as brand-voice. |

## The cell contract

`engine/cells/copywriter.py` — `build_copywriter_cell(brand_voice_context=…,
approved_claims=…, …)` returns a typed `Cell[CopywriterDrafts]`:

- **Output:** `CopywriterDrafts{ platform, angle, variants: [CopyVariant{pattern,
  pillar, hook, caption, call_to_action}] }`.
- **Validators:** `copywriter_validators()` — variety, filled, distinct hooks,
  platform length, and the **S3 AI-flagger** over every variant.
- **Input:** the run prompt carries the scored winning angle; the brand-voice
  context + approved claims are composed into the instructions by the harness.

## Quick reference

- Mold winning patterns into the voice; **voice beats pattern.**
- 2–4 variants, **distinct hooks**, each tied to a pillar.
- Approved claims only; missing claim → flag, don't write.
- No AI tells (S3 blocks them); respect platform length.
- Gated downstream by jury + confidence — never auto-ships.

## Provenance

Hook/CTA + content-repurposing **patterns** retargeted to tattoo captions and
re-authored into our format; grounded in `docs/skills/winning-strategies-kb.md`.
No third-party skill code is vendored — pattern adoption only; see `VETTING.md`.
Composes S2 brand-voice + S3 AI-flagger.
