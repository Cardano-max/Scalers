---
name: outreach-sequence-builder
description: Use when the outreach engine plans a cold-email sequence for a tattoo
  studio. Builds a personalized, CAPPED, suppression-first 4-touch sequence
  (day 0/+3/+5/+7) with warmup-aware caps, RFC-8058 one-click unsubscribe on every
  touch, and a reply/bounce/unsubscribe hard-stop — escalated for review, never
  auto-sent. Trigger words: outreach, cold email, sequence, follow-up, cadence,
  warmup, suppression, deliverability, sending cap.
license: pattern-only re-authoring — see NOTICE
pinned: ORIGINAL  # no upstream code vendored (pattern-only)
---

# Outreach Sequence Builder (tattoo studio)

## Overview

This skill makes the outreach engine plan a **safe, capped, personalized** cold
sequence — not a spray. It is **prompt-only methodology**; the enforcement
(suppression-first, caps/spacing, hard-stop, over-personalization guard) is the
deterministic `engine/outreach/` policy, so the rules cannot be prompted away
(the 1mk.3 pattern). The per-touch **copy** is the writer's copywriter cell
(1mk.5) grounded in brand-voice (S2) and checked by the S3 AI-flagger validator +
jury — this skill owns *structure + eligibility + safety*, not prose.

> "outreach-sequence-builder: buying signal in, 4-6 touchpoint sequence out across
> email, LinkedIn, phone" — Sam_Tech1, r/ClaudeAI (verbatim, winning-strategies-kb.md
> — the upstream pattern; we mine the pattern only and constrain it to email)

> "Keep the human approval gate on outreach: suppression-first, capped +
> personalized sequences, all sends gated through our harness (CAN-SPAM/GDPR) —
> never auto-send." — skills-dos-donts.md (our hard rule)

## When to use

Load when building or advancing an outreach sequence for a tenant. Outreach is
**consent-aware** (past inquiry, event signup, opted-in partner) — not anonymous
spray. Do **not** use it to write the email copy (copywriter, 1mk.5) or for DMs
(DMs always escalate).

Conditionally loaded by state: outreach-engine cells planning a sequence.

## The rules it enforces (spec §5, via `engine/outreach/policy.py`)

1. **Suppression-first** — the do-not-contact list is checked *before* anything
   else; a hit is skipped, full stop.
2. **Deliverability verification** — `cold-email-verifier` runs; undeliverable is
   blocked, risky escalates with a warning.
3. **Capped 4-touch sequence** — day **0 / +3 / +5 / +7** (widening gaps);
   warmup ramp ~8→18→28→40/inbox/day (25 on consumer Gmail); hard system caps are
   not targets.
4. **Hard-stop** — reply / bounce / unsubscribe / spam-complaint halts the
   sequence immediately.
5. **RFC 8058 one-click unsubscribe on every touch**; honor opt-out ≤2 days.
6. **No creepy personalization** — the over-personalization guard strips
   private/identifying signals and caps refs/touch.
7. **Never auto-send** — every plan is routed to review (bead 439 hold: all
   channels manual until rvy.7+rvy.8 pass on a real gold set).

## Method (what good looks like)

- Pull the prospect + allowed (public/opt-in) signals; the guard screens them.
- Plan 4 touches with distinct purpose (intro / value-add / soft-CTA / break-up),
  each referencing ≤2 allowed signals so it reads relevant, not surveilled.
- Hand the per-touch brief to the copywriter (S2 voice); the copy passes S3 + jury.
- Emit an `OutreachPlan` (PII-free; prospect referenced by hash) → review queue.

## Edge cases

- Suppressed → skip. Undeliverable → block. Risky → escalate with caution.
- Reply/bounce/unsubscribe → hard-stop. No safe signals → generic on-voice copy.
- Thresholds to protect the domain: spam complaints <0.10% (operate <0.08%),
  bounce <2% — enforced by suppression + verification + caps, monitored by growth.

## Output contract

Returns an `OutreachPlan` (disposition, suppression, verification, sequence,
warnings, `routed_to="review"`). Never sends. The send connector is the harness
side-effect boundary (exactly-once), wired by eng and gated by 439.
