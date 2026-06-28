# Verification — copywriter (1mk.5)

**Claim under test (AC):** the copywriter cell molds a winning angle into the
artist voice (uses S2) and its output passes the S3 AI-flagger validator + jury;
edge cases handled.

## How to reproduce

```bash
# from the engine dir, with the engine venv
python -m pytest tests/test_copywriter.py -q     # 10 passed
```

Driven offline with Pydantic-AI `FunctionModel` (no API key), same pattern as
`test_humanize_cell.py`. Verified: **10 passed in 0.17s**. Related suites green
after the change: `test_ai_flagger.py` + `test_humanize_cell.py` (15 passed),
`test_content_brief_cell.py` (11 passed).

## What it proves

**S3 AI-flagger composition + deterministic bank** (pure code, no model):

| Test | Proves |
|---|---|
| `test_good_drafts_pass_the_bank` | A clean, distinct, on-length draft set passes. |
| `test_ai_tell_in_variant_is_flagged` | An em-dash + generic transition in a variant is an `ai_flagger` ERROR (S3 runs over nested variants). |
| `test_duplicate_hooks_are_flagged` | Over-templating (identical hooks) → `hooks_distinct` ERROR. |
| `test_too_few_variants_is_an_error` | < 2 variants → `variants_count` ERROR (variety enforced). |
| `test_overlong_hook_is_flagged` | A 20-word hook → `platform_length` ERROR. |
| `test_facebook_uses_its_own_caption_cap` | A 3000-char caption errors on IG (2200) but passes on FB (5000) — per-platform limits. |

**The cell, offline:**

| Test | Proves |
|---|---|
| `test_returns_typed_drafts` | Returns a typed `CopywriterDrafts` with the variants. |
| `test_slop_draft_is_repaired_then_accepted` | A slop draft (em-dash + transition) is flagged → repaired → accepted; `repairs >= 1`, `first_pass_valid is False`. |
| `test_over_templated_draft_is_repaired` | A duplicate-hook draft is repaired into distinct variants. |

**S2 brand-voice composition:**

| Test | Proves |
|---|---|
| `test_instructions_compose_brand_voice_and_claims` | The resolved brand-voice context + approved claims are injected into the cell instructions, and the "BRAND VOICE WINS" rule (pattern-vs-voice edge case) is present. |

## Scope / honesty notes

- This verifies the **deterministic surface** (validators, repair loop, S2/S3
  composition) offline. The **eval-gate** (brand-voice quality on the Phase-2 gold
  set, rvy.7/.8) is the **release gate** and is **not run here** — it is pending
  the gold set. Per the autonomy hold (bead 439), **no production use** until evals
  pass + sec S1 + operator adoption.
- The cell's input contract (`angle` + brand-voice context) is the seam where
  **growth** (Tier-2 research → scored angle) and **pmm** (positioning pack → S2
  DNA) plug in.
