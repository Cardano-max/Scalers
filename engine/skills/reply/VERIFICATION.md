# Verification — reply (1mk.6)

**Claim under test (AC):** the reply cell drafts on-voice short replies handling
objections; **comments auto within threshold, DMs always escalate**; uses S2+S3;
edge cases handled.

## How to reproduce

```bash
# from the engine dir, with the engine venv
python -m pytest tests/test_reply.py -q     # 17 passed
```

Offline (Pydantic-AI `FunctionModel`, no API key). Verified **17 passed in 0.14s**.

## What it proves

**THE HARD RULE — routing (`route_reply`):**

| Test | Proves |
|---|---|
| `test_dm_always_escalates_even_at_full_confidence` | A DM at **confidence 1.0** still routes to REVIEW (human). |
| `test_comment_auto_within_threshold` | A clean comment ≥ threshold → AUTO (`esc=none`). |
| `test_comment_below_threshold_reviews` | Comment < threshold → REVIEW (`below_threshold`). |
| `test_comment_safety_veto_reviews` | Comment with a safety veto → REVIEW (`safety`), even at 0.99. |
| `test_comment_needs_expertise_reviews` | `needs_human_expertise` → REVIEW. |
| `test_comment_dial_review_forces_review` | Channel dial = REVIEW forces review. |

**THE HARD RULE — cell boundary + bank:**

| Test | Proves |
|---|---|
| `test_dm_without_escalate_is_an_error` | A DM draft with `recommend_escalate=False` is a `dm_requires_escalation` ERROR. |
| `test_cell_repairs_dm_that_forgets_to_escalate` | The cell **repairs** a DM that forgot to escalate until `recommend_escalate=True`. |
| `test_expertise_without_escalate_is_an_error` | Expertise without escalate is an ERROR. |

**Safety pre-screen + S3 + quality:**

| Test | Proves |
|---|---|
| `test_screen_flags_hostile_and_threats` | Hostile/abuse + threats → `SafetyVerdict.VETO`. |
| `test_screen_passes_benign` | Benign / empty → `PASS` (not an approval — escalate-only). |
| `test_ai_tell_in_reply_is_flagged` | An em-dash + transition in the reply → `ai_flagger` ERROR (S3). |
| `test_overlong_reply_is_flagged` | A 60-word reply → `word_count_between` ERROR (short-social cap). |
| `test_clean_comment_passes` | A clean, short, on-voice comment passes the bank. |

**Cell offline + S2 composition:**

| Test | Proves |
|---|---|
| `test_cell_returns_typed_reply` | Returns a typed `ReplyDraft`. |
| `test_cell_repairs_slop_reply` | A slop reply is flagged → repaired → accepted. |
| `test_instructions_compose_brand_voice_and_hard_rule` | Brand-voice context + approved claims are injected, and "DMs ALWAYS go to a human" is in the instructions. |

## Scope / honesty notes

- Verifies the **deterministic surface** (HARD-RULE routing + boundary, safety
  pre-screen, S2/S3 composition, repair loop) offline.
- The **release gate** — **reply-safety = 0 violations on the red-team**
  (rvy.7/.8) — is **not run here** (the red-team set isn't built yet). Per the
  autonomy hold (bead 439): **no production use** until it passes + sec S1 +
  operator adoption.
- The deterministic hostile pre-screen is conservative and **escalate-only**; it
  is not the safety authority (that's the AUTON-04 jury) and is not a substitute
  for the red-team eval.
