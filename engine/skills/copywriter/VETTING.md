# Vetting record — copywriter (CustomerAcq-1mk.5)

The writer-side vetting/registration record for the `copywriter` skill, prepared
for the 1mk.1 supply-chain gate. **sec owns the S1 sign-off.** The canonical
registry row lives in `docs/skills/registry.md` (sec). **ELIGIBLE != IN USE**: no
agent loads this skill until the operator approves adoption + agent assignment,
and not before the eval gold-set passes.

| Field | Value |
|-------|-------|
| Skill | `copywriter` (hook/CTA from winning patterns) |
| Upstream source | content-repurposing / "cook-the-blog" hook+CTA **patterns** (R&D set: emilyxhug content-repurposing, tenegoacademy omnichannel chains, curated hook skills). **No third-party skill code vendored.** |
| License | N/A — pattern adoption only; original content. If any verbatim upstream text is later vendored, sec pins the exact source + commit + license first. |
| Pinned commit | N/A (no code vendored) |
| Our-format path | `engine/skills/copywriter/SKILL.md` (+ `references/hook-cta-patterns.md`) |
| Enforcement code | `engine/cells/copywriter.py` (cell + deterministic validator bank) |
| Composes | **S2 brand-voice** (`1mk.2`, in-use) + **S3 AI-flagger** (`1mk.3`, in-use) |
| Eval-gate status | **PENDING** — gate = the Phase-2 eval gold-set (rvy.7 suite / rvy.8 calibration); not yet run. Deterministic bank demonstrated by `tests/test_copywriter.py` (10 passed). |
| sec sign-off (S1) | **PENDING** — instructions-only skill; review for prompt-injection / off-policy content + confirm pattern-only (nothing vendored). |
| Status | DRAFT / authoring complete — **not eligible, not in use** (autonomy hold, bead 439). |

## 4-step gate

1. **Read SKILL.md + every shipped script** — the only executable content is
   **our own** `engine/cells/copywriter.py` (deterministic validators + a typed
   cell). No upstream scripts; pattern adoption only.
2. **Strip/sandbox unintended network/file/exec** — **nothing to strip.** The
   validators are pure-Python (regex/string, no I/O, no network); the one model
   call is the cell's draft, temp-0, behind the deterministic bank + harness gates.
3. **Re-author into our format + pin** — done. Patterns re-authored to our format,
   grounded in `docs/skills/winning-strategies-kb.md`. No upstream commit to pin
   (no code vendored); flagged for sec if that changes.
4. **Eval gold-set gate** — the RELEASE gate. No production writing use until the
   copywriter clears the eval gold-set (rvy.7/.8). Authoring proceeds now; release
   is held.

## What was stripped

Nothing. No third-party code was adopted — only patterns (instructions). The
re-authored skill + cell are our own; read for prompt-injection / off-policy
content, none found.

## Coordination

- **growth (Tier-2 research):** feeds the *scored winning angle* this cell
  consumes — the cell's input contract is `angle` + the brand-voice context.
- **pmm (positioning pack):** owns the per-tenant brand DNA (S2) the cell grounds
  on; the copywriter reads it, never overrides it.

## Notes

- **ELIGIBLE != IN USE** and **eval is the release gate**: the deterministic bank
  is safe (pure code), but the cell makes a model call and must clear the eval
  gold-set + sec S1 + operator adoption before any agent uses it in production.
