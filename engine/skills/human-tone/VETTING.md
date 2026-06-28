# Vetting record — human-tone (CustomerAcq-1mk.3)

This is the eng3-side vetting/registration record for the `human-tone` skill,
prepared for the 1mk.1 supply-chain gate. **sec owns the S1 sign-off**; this
skill is **NOT registered for agent use** until sec signs off and a row exists in
the 1mk.1 skill registry. Recorded here against the 4-step gate so sec can
complete it.

| Field | Value |
|-------|-------|
| Skill | `human-tone` |
| Upstream source | Varnan-Tech/opendirectory — "human-tone" |
| License | MIT |
| Pinned commit | **TBD by sec at registration** (record the exact upstream commit) |
| Our-format path | `engine/skills/human-tone/SKILL.md` |
| Enforcement code | `engine/cells/ai_flagger.py` (validator), `engine/cells/humanize.py` (rewrite cell) |
| Eval-gate status | PASS — labeled-set test `tests/test_ai_flagger.py::test_labeled_set_separates_slop_from_human` (recall 1.0, false-positive rate 0.0 on the seed set) |
| sec sign-off (S1) | **PENDING** |
| Registered for | none yet (blocked on sign-off) |

## 4-step gate

1. **Read SKILL.md + every shipped script** — upstream is **pure markdown, no
   scripts.** There is no executable content shipped by the skill.
2. **Strip/sandbox unintended network/file/exec** — **nothing to strip.** No
   network calls, no file access, no code execution in the upstream skill. Our
   re-authored enforcement is pure-Python regex (no I/O, no network) plus one
   temp-0 model call in the rewrite cell (the only model use; gated behind the
   deterministic flagger).
3. **Re-author into our format + pin** — done (`SKILL.md`), re-authored with our
   determinism rules and brand-voice intent. Pin the upstream commit at
   registration (sec).
4. **Eval gold-set gate** — the deterministic detector is demonstrated on a
   labeled set (see Eval-gate status). When the Phase-2 eval gold set is wired,
   re-run against it before final registration.

## What was stripped

Nothing. The upstream skill is instructions-only markdown with no scripts,
network, or file/exec surface. It was still read for prompt-injection / off-policy
content; none found. The risk profile is "lowest" (pure markdown), matching the
R&D pick.

## Notes for sec

- No agent may load/use this skill until the 1mk.1 registry has a row with S1
  sign-off + eval-gate status for it.
- The validator (`ai_flagger`) is safe to wire into the bank now (pure code, no
  model, no I/O); the *rewrite cell* makes a model call and should ship behind the
  same autonomy/eval gates as any other cell.
