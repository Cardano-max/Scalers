# Vetting record — reply (CustomerAcq-1mk.6)

The writer-side vetting/registration record for the `reply` skill, prepared for
the 1mk.1 supply-chain gate. **sec owns the S1 sign-off.** The canonical registry
row lives in `docs/skills/registry.md` (sec). **ELIGIBLE != IN USE**: no agent
loads this skill until the operator approves adoption + agent assignment, and not
before the eval (reply-safety) gate passes.

| Field | Value |
|-------|-------|
| Skill | `reply` (short comment/DM replies; discovery + objection-handling) |
| Upstream source | louisblythe/Sales-Skills (discovery + objection-handling) **patterns**, retargeted to short social replies. **No third-party skill code vendored.** |
| License | N/A — pattern adoption only; original content. NOTE (from skills-dos-donts.md): the upstream fork ships **no LICENSE** (MIT carries by lineage only) — so a LICENSE must be added before any *verbatim* redistribution. We vendor nothing, so this does not gate us; flagged for sec. |
| Pinned commit | N/A (no code vendored) — sec to pin if any verbatim text is later adopted. |
| Our-format path | `engine/skills/reply/SKILL.md` (+ `references/discovery-objection-patterns.md`) |
| Enforcement code | `engine/cells/reply.py` (cell + validators + `route_reply` + `screen_incoming`) |
| Composes | **S2 brand-voice** (`1mk.2`, in-use) + **S3 AI-flagger** (`1mk.3`, in-use); routing layers on `harness.router.route` + `autonomy.decision`. |
| Eval-gate status | **PENDING** — RELEASE gate = **reply-safety 0 violations on the red-team** (rvy.7/.8). Deterministic surface demonstrated by `tests/test_reply.py` (17 passed). |
| sec sign-off (S1) | **PENDING** — instructions-only; review for prompt-injection / off-policy + the no-LICENSE-upstream note above. |
| Status | DRAFT / authoring complete — **not eligible, not in use** (autonomy hold, bead 439). |

## 4-step gate

1. **Read SKILL.md + every shipped script** — the only executable content is
   **our own** `engine/cells/reply.py` (validators + pure routing + pre-screen).
   No upstream scripts; pattern adoption only.
2. **Strip/sandbox unintended network/file/exec** — **nothing to strip.** All
   pure-Python (regex/string, no I/O, no network); the one model call is the
   draft (temp-0), behind the deterministic bank + the handoff rules + harness
   gates.
3. **Re-author into our format + pin** — done; retargeted to short social replies,
   grounded in the KB. No upstream commit to pin (no code vendored).
4. **Eval gate** — **reply-safety = 0 red-team violations** is the release gate.
   No production use until it passes.

## Safety posture (why this skill is sensitive)

- **HARD RULE in code, two ways:** DMs always route to a human (cell-boundary
  validator + routing), so a DM can never auto-send regardless of confidence.
- **Hostile/troll pre-screen** (`screen_incoming`) is **escalate-only** — it can
  raise the safety bar but never clears a reply for auto-send. The AUTON-04
  cross-family jury is the safety authority.
- **Expertise handoff:** medical/healing/legal/pricing questions force review.

## Coordination

- **growth (1mk.7 outreach):** shares the engagement/handoff philosophy
  (suppression-first, human-gated). Reply handles inbound; outreach handles
  outbound — both keep the human approval gate.
