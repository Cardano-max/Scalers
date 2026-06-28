# Verification — brand-voice (1mk.2)

**Claim under test (AC):** *a draft cell demonstrably starts from real brand
context.* Plus the SKILL.md edge cases.

## How to reproduce

```bash
# from the Scalers repo root, on this branch
python skills/brand-voice/verify/demo_brand_grounding.py   # exits 0 on success
```

stdlib-only (Python 3.11+ for `tomllib`); no engine venv, no LLM, no network.
Verified on Python 3.13.12, Windows.

## What it proves

1. **Baseline (RED).** The shipped `engine/cells/content_brief.py` instruction
   mentions "brand voice" but carries **no actual brand data** — the
   generic-SaaS-voice failure this skill exists to fix
   (emilyxhug, winning-strategies-kb.md). The demo asserts the baseline contains
   none of the artist's specifics. ✔

2. **Grounded (GREEN).** Resolving the real `ink-studio` pack
   (`engine/config/packs/ink-studio.toml` → `voice.skill = "brand-voice/ink-studio"`)
   assembles a system prompt that puts the artist's brand DNA + on-voice examples
   **before** the task. The demo asserts the grounded prompt contains:

   | Grounding element | Needle | Result |
   |---|---|---|
   | Artist identity | `Mara Vance` | PASS |
   | Positioning promise | `quiet personal story` | PASS |
   | An approved claim | `Free 20-minute consultation before every booking` | PASS |
   | A do-not ban | `unleash` | PASS |
   | An on-voice example | `grandmother's handwriting` | PASS |
   | Original task preserved (ground, don't replace) | first sentence of `_INSTRUCTIONS` | PASS |

   5 on-voice grounding examples loaded from the pack's example set;
   `skill_ref='brand-voice/ink-studio'`; `degraded=False`.

3. **Edge case — new artist, no DNA.** Resolving a tenant whose pack points at a
   brand-voice ref with no DNA file **gracefully degrades to positioning-only**,
   loads zero examples, and injects a note instructing the cell to lower confidence
   so the router queues it for review. Asserted PASS.

```
RESULT
All grounding + edge-case assertions passed.
A draft cell now demonstrably STARTS FROM real brand context.
EXIT=0
```

4. **Security — path traversal rejected (sec S1 hardening).** `tenant_id` and the
   artist segment of `skill_ref` are validated against `^[A-Za-z0-9][A-Za-z0-9_-]*$`
   and a containment check before any filesystem access. The demo asserts that
   `../../etc/passwd`, `..`, `a/b`, `x.toml`, `/abs`, `C:\win` (as `tenant_id`) and
   a malicious pack `skill = "brand-voice/../../../../secrets"` all raise
   `BrandVoiceError`. Legit ids (`ink-studio`, `newbie`) still resolve. All PASS.

## Scope / honesty notes

- This verifies the **grounding-assembly contract** that 1mk.2 owns: the skill +
  per-tenant DNA + pack + examples resolve into the brand context a cell starts
  from. The reference resolver (`resolve_brand_voice.py`) is the contract the
  **engine** wires into its on-demand skill load (follow-up eng bead); the
  pack/schema seam (`VoiceRef`) already exists.
- The **eval-gate** (brand-voice ≥90% on the `rvy.4` holdout, κ≥0.6) is **not run
  here** — `rvy.4` is OPEN (holdout not built). Registration stays gated on it and
  on sec sign-off (`1mk.1`). This deliverable is authored + demonstrated, **not
  registered / not in use.**
- The remaining edge cases in SKILL.md (conflicting do/do-not → bans win + flag;
  claim-not-in-approved-set → block + escalate; multi-artist → load exactly one
  artist) are specified as required cell behavior; they exercise at draft time
  (cell + AI-flagger 1mk.3), not in this static-assembly demo.
```
