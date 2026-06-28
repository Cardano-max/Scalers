---
name: human-tone
description: Detect and remove AI-writing tells (em-dashes, contrast framing,
  rule-of-three, generic transitions) so copy reads as written by a real person.
  Enforced as a deterministic validator + a voice-QA rewrite cell.
upstream: Varnan-Tech/opendirectory — "human-tone" (MIT)
pinned: 9c30f79eb975c50a97bed10b47e14f18116a3e3b  # pure markdown; no scripts
status: ELIGIBLE — sec S1 APPROVED; not IN USE until operator adoption + agent assignment
---

# human-tone (re-authored, CustomerAcq-1mk.3)

The operator's hard rule: **no AI slop ships.** This skill enforces the
human-tone bar in code, not by hope. It exists in two forms so detection is
deterministic and the fix is bounded:

## (a) Deterministic validator — `engine/cells/ai_flagger.py`

Pure regex/string rules, **no model call**, fully reproducible. Plugs into the
HARN-02 validator bank and feeds the validator-pass-rate. Detects:

| Tell | Example | Default severity |
|------|---------|------------------|
| em-dash / double-hyphen | `we listen — then create` | ERROR |
| contrast framing | `it's not X, it's Y` / `not just X but Y` | ERROR |
| generic transition | `Moreover`, `In conclusion`, `When it comes to` | ERROR |
| rule-of-three | `skill, passion, and precision` | WARN (advisory) |

**Tunable** via `FlaggerConfig`: per-kind severities, `max_em_dashes`,
`max_triads` (one triad is allowed by default), and an `allowlist` of exempt
substrings for legitimate use. Non-English text skips the English wordlist
detectors (contrast/transition) to avoid foreign-language false positives.

A safe, meaning-preserving deterministic strip (`normalize_ai_tells`) fixes only
the em-dash subset; semantic tells are left for the rewrite cell.

## (b) Humanize voice-QA rewrite cell — `engine/cells/humanize.py`

A typed `Cell[HumanizedDraft]` (pinned model, temp-0) that rewrites a flagged
draft toward human tone. Its own output is re-checked by the AI-flagger (an ERROR
validator in its bank) and by a claims-preservation validator, so a rewrite that
still reads as slop — or that drops an approved claim — is repaired or fails on a
code path. It must never add new claims.

## Pipeline ordering

Run the deterministic flagger first (cheap, no model); route only flagged drafts
through the rewrite cell (a model call). Re-flag the rewrite output.

## Determinism

Detection is pure code (reproducible, no model). Only the rewrite uses a model,
at temperature 0 against a pinned id, and its output must clear the deterministic
flagger before it can ship.
