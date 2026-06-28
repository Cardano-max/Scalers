---
name: reply
description: Use when the engagement cell drafts a SHORT reply to a social comment or DM for a tattoo artist — discovery questions and objection-handling in the artist's voice. Enforces the handoff policy: comments may auto within threshold, DMs ALWAYS go to a human. Trigger words: reply, comment, DM, engagement, objection, discovery, handoff, escalate, troll, hostile.
license: pattern-adoption (no upstream code vendored — see VETTING.md)
pinned: ORIGINAL
---

# Reply (comment / DM)

## Overview

Drafts **short** social replies — a comment or a DM — in the artist's voice,
using sales **discovery** (ask before you pitch) and **objection-handling**
(acknowledge → answer with approved claims → soft next step). Replies are gated;
they are never the last word before a human or the autonomy bar.

## THE HARD RULE (non-negotiable)

> **Comments** may auto-reply **within the confidence threshold.**
> **DMs ALWAYS route to a human — regardless of confidence. A DM is never auto-sent.**

This is enforced in code two ways so it cannot be bypassed:

1. **Cell boundary** — `dm_requires_escalation` makes a DM draft with
   `recommend_escalate=False` an ERROR (the draft is repaired until the DM is
   marked for handoff).
2. **Routing** — `route_reply` returns `REVIEW` for **every** DM, even at
   confidence 1.0, before the confidence/dial logic runs.

Violating the letter of this rule is violating the spirit of it. There is no
"confident enough DM," no "trivial DM," no "just this once." DM → human. Always.

## When to use

Load on the **engagement cell** when drafting a reply to an incoming comment or
DM. Not for: posting copy (copywriter), outreach email (outreach), research.

Conditionally loaded by the harness on the engagement cell.

## Composition (depends on two in-use skills)

| Dep | Role here | Where |
|---|---|---|
| **S2 brand-voice** (`1mk.2`) | The reply is written in the artist's voice; only approved claims. Context injected into instructions. | `skills/brand-voice/` |
| **S3 AI-flagger** (`1mk.3`) | `detect_ai_tells` runs over the reply text as an ERROR validator — no AI slop in public replies. | `engine/cells/ai_flagger.py` |

Downstream, the harness jury + confidence/autonomy gate route the result
(`route_reply` composes the canonical pure-code router). **Never auto-ships** on
its own (autonomy hold, bead 439).

## The recipe

1. **Read the brand voice first** (S2). Reply as the artist, not a brand account.
2. **Discovery before pitch.** For a vague or curious comment, ask ONE question to
   understand what they want — don't pitch into ambiguity.
3. **Handle objections honestly.** Acknowledge the concern, answer with an
   approved claim, offer a soft next step. Never argue or get defensive.
4. **Decide the surface.** If it's a DM, set `recommend_escalate=true` and write a
   reply for the human to send. If it needs human/medical/safety/legal judgement,
   set `needs_human_expertise=true` and escalate.
5. **Keep it short and clean.** One thought, ≤45 words, no AI tells, no placeholders.

See `references/discovery-objection-patterns.md` for the retargeted patterns.

## Do / Do-not (from docs/skills/skills-dos-donts.md)

**Do:** ask a discovery question when the need is unclear; answer objections with
approved claims only; keep replies short and human; route every DM to a human;
escalate anything needing real expertise; stay neutral with trolls.

**Do-not:** auto-send a DM (ever); argue with or feed a troll; invent prices,
offers, or guarantees; ship AI tells (S3 blocks them); write a wall of text;
give medical/healing advice as if from a professional.

## Edge cases (required behavior)

| Situation | Required behavior |
|---|---|
| **Hostile / troll comment** | The deterministic `screen_incoming` pre-screen returns a safety veto → `route_reply` forces REVIEW. Keep any draft neutral; do not engage. The pre-screen can only *escalate*, never approve. |
| **Question needs human expertise** (healing problems, allergic reaction, pain/medical, pricing commitment, legal/consent) | `needs_human_expertise=true` → `recommend_escalate=true` → REVIEW. |
| **24h IG DM window** | DMs already always go to a human; the connector tracks the platform's 24-hour messaging window, so the human sends within policy. If the window has expired, flag it on the handoff rather than attempting an out-of-policy send. |
| **Multi-comment thread** | Reply to the *person*, not each line; one reply per commenter; use the thread context the harness passes; don't repeat the same canned line across a thread. |

## The cell contract

`engine/cells/reply.py` — `build_reply_cell(brand_voice_context=…,
approved_claims=…)` returns a typed `Cell[ReplyDraft]`:

- **Output:** `ReplyDraft{ surface, text, intent, discovery_question,
  needs_human_expertise, recommend_escalate, escalation_reason }`.
- **Validators:** `reply_validators()` — non-empty, no-placeholder, banned
  phrases, length (≤45 words), **S3 AI-flagger**, **`dm_requires_escalation`**,
  `expertise_requires_escalation`.
- **Routing:** `route_reply(draft, confidence=…, …)` — DMs always REVIEW; comments
  use the canonical router; safety veto / expertise force REVIEW.
- **Pre-screen:** `screen_incoming(text)` — deterministic hostile/troll → safety
  verdict (escalate-only).

## Quick reference

- **DM → human. Always.** Comments → auto only within threshold.
- Discovery before pitch; objections answered with approved claims only.
- ≤45 words, no AI tells (S3), no invented offers.
- Hostile → escalate (neutral); expertise → escalate.
- Gated by jury + confidence downstream — never auto-ships.

## Provenance

Discovery + objection-handling **patterns** re-authored from
louisblythe/Sales-Skills into our format and retargeted to short social replies;
grounded in `docs/skills/winning-strategies-kb.md`. No third-party code vendored —
pattern adoption only; see `VETTING.md`. Composes S2 brand-voice + S3 AI-flagger.
