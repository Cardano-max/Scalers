# Vetting record — outreach-sequence-builder (CustomerAcq-1mk.7)

Growth-side record for the 1mk.1 gate. **sec owns the S1 sign-off** (SUBMITTED).
Canonical row: `docs/skills/registry.md`. **ELIGIBLE ≠ IN USE**; additionally
**RELEASE-GATED by bead 439** — no real sends until rvy.7 (eval suite) + rvy.8
(calibration) pass on a real outreach gold set.

| Field | Value |
|-------|-------|
| Skill | `outreach-sequence-builder` |
| Upstream pattern | "outreach-sequence-builder" (r/ClaudeAI 20-skills list) — mine-patterns-only |
| Pinned commit | `<PIN-AT-ADOPTION>` — sec resolves/verifies real 40-hex SHA at fetch |
| Skill type | Pattern-only re-authoring; prompt-only (enforcement = `engine/outreach/`) |
| Our-format path | `skills/outreach-sequence-builder/` + `engine/outreach/` |
| sec sign-off (S1) | **SUBMITTED — pending sec verification** |
| Eval-gate status | **PENDING-on-gold-set** (`evals/gold/outreach-smoke.jsonl`; holdout + calibration = rvy.7/.8) |
| Release gate | **bead 439** — channels MANUAL/escalate until eval-green; no auto-send |
| Status | **HELD** — sec S1 + eval-gate + 439 + operator adopt-approval pending |

## 4-step gate

1. **READ** — pattern source read; **no upstream scripts vendored** (the
   money/send-capable repos in this family are REJECTED in the registry —
   `coldoutboundskills` spends money, marketingskills email CLIs send real mail).
2. **STRIP** — nothing vendored to strip; the skill is original prompt-only
   methodology. Sending is the harness side-effect boundary, gated by 439 — never
   an off-the-shelf sender; no money/exec/credential surface.
3. **RE-AUTHOR + PIN** — original methodology + deterministic enforcement in
   `engine/outreach/` (suppression-first, caps/spacing, hard-stop, creepy guard).
   Pin required (sec fills SHA at fetch).
4. **EVAL-GATE** — `evals/gold/outreach-smoke.jsonl` smoke set (replayed by
   `test_outreach_gold_smoke.py`); the real holdout + calibration gates are
   rvy.7/.8. **PENDING.**

## What was stripped

Nothing vendored (mine-patterns-only). The capability we explicitly do NOT take:
real sending / money-spending CLIs (REJECTED family). Residual surface: **none**
(prompt-only + deterministic in-house policy). Sends are 439-gated.
