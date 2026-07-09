---
name: customer-psychology
description: Use when a run classifies a lead's psychology before drafting — decide WHERE
  a customer sits (umbrella category + primary objection + readiness stage) grounded in
  their OWN real conversation/CRM facts, with hard anti-fabrication (every read is
  stated/inferred/insufficient-signal with a verbatim evidence span). Trigger words:
  psych, objection, readiness, where-customer-sits, lead classification, analyst.
upstream: ORIGINAL — first-party (wraps engine/studio/psych_profile.py; no third-party code)
pinned: ORIGINAL
license: first-party (internal)
status: IN-VETTING — PENDING sec sign-off (NOT registered; live path still imports psych_profile.py directly)
---

# customer-psychology (skillpack SCAFFOLD — NOT registered)

This is a **progressive-disclosure wrapper** around the already-live, sec-safe
customer-psychology analyst (`engine/studio/psych_profile.py`, 13 passing tests). It
exists so the analyst can be **loaded on demand** (only when a run does lead
classification) instead of being imported unconditionally, and so it can move through the
sec-owned vetting gate as a first-class skillpack.

## Status — do NOT treat this as usable

Per the supply-chain HARD RULE (`docs/skills/registry.md`), a skill is usable **only** with
a `REGISTERED — IN USE` row. This pack has a **DRAFT `IN-VETTING — PENDING sec sign-off`**
row. It is therefore **not usable yet**:

- No sec security sign-off recorded.
- No eval-gate result (the analyst's 13 tests are its floor, but the pack has no gold-set).
- No operator adoption.

**The live system does NOT depend on this pack.** `psych_profile.analyze_customer` is still
imported directly by the provided-leads run (`studio/agui.py`). This scaffold is additive;
`scripts/check_skill_registry.py` must continue to pass with it present.

## Provenance

**First-party / ORIGINAL.** No third-party code is vendored — the pack wraps our own
`psych_profile.py`. There is NO upstream repo/commit to pin, so `pinned: ORIGINAL` (the
first-party precedent set by `copywriter` / `reply`) — a fabricated 40-hex SHA would itself
violate the no-fabrication gate. The registry row's pin is likewise `ORIGINAL`, matching this
`SKILL.md`. On any change to the wrapped analyst, sec re-vets.

## What it wraps

`psych_profile.analyze_customer(facts, conversation, ...) -> PsychProfile` — the deep,
evidence-grounded read. The wrapper adds NOTHING to the model's freedom: it is a thin
loader (`loader.py`) that imports and calls the existing function. All anti-fabrication
guarantees live in `psych_profile.py` and are unchanged:

- every dimension tagged `stated` | `inferred` | `insufficient-signal` with a verbatim span;
- a deterministic keyless floor; an optional LLM pass validated against the lead's own text;
- no signal → the value is dropped, never invented.

## Progressive disclosure

`loader.load()` imports `psych_profile` lazily and returns its `analyze_customer` callable,
so nothing is loaded until a run actually classifies a lead. This is the ONLY behavior the
pack adds. See `manifest.json` for the pack contract and `loader.py` for the seam.

## The gate sec still owns (do NOT self-certify)

sec — not eng — flips this to `REGISTERED — IN USE`, after: (1) reading the wrapped code +
loader for injection/off-policy, (2) confirming nothing new is introduced beyond the vetted
analyst, (3) an eval-gate result, (4) operator adoption. Until then this pack is
scaffold-only.
