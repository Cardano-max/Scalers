# Skill-use enforcement — CLAUDE.md wiring spec (1mk.10)

**Author:** sec · **Applies to:** super (who owns + edits the agent `CLAUDE.md` files) · **Backs:** the 1mk.1 vetting gate + the CI check (`scripts/check_skill_registry.py`).

This spec is the **prompt-layer** half of the `no registry row → no skill use` guardrail. The **CI** half (build fails on unregistered use / provenance drift) ships in this same PR. Both are needed: CI catches what lands in the repo; the CLAUDE.md rule governs what an agent loads at runtime.

super: paste the block below verbatim into each agent's `CLAUDE.md`. No code changes — this is instruction text only.

---

## Where to add it

| File | Why |
|---|---|
| Root `CLAUDE.md` (repo + each agent-root that the orchestrator reads) | Baseline rule for every agent. |
| `writer/CLAUDE.md` | Loads brand-voice + human-tone (writing cells). |
| `eng1,2,3/CLAUDE.md` | May wire skills into cells/harness. |
| `growth/CLAUDE.md` | Owns the research skills (network adapters). |
| `qa1,2/CLAUDE.md` | Must verify skills are registered before testing them as in-use. |

(Skip an agent only if it never loads or references skills.)

## The block to paste (verbatim)

```markdown
## Skill use — HARD RULE (supply-chain gate, sec-owned)

A skill (third-party OR adapted) may be loaded, referenced, or executed by an
agent **ONLY** if it has a row in `docs/skills/registry.md` that is **REGISTERED — IN USE**,
i.e. all of:
  - **sec security sign-off** recorded, and
  - **eval-gate green** (or an explicit operator-authorized PENDING-on-eval-pipeline note), and
  - **operator adoption** recorded, and
  - a **real 40-hex upstream pin** that matches the skill's own `SKILL.md` `pinned:` field.

**No row → no use.** If a skill has no row, or its row is `ELIGIBLE` / `HELD` /
`REJECTED`, do **not** load it, reference it in a cell/prompt, or run any script it
ships. An `ELIGIBLE` skill is vetted but **not yet adopted** — it is not usable.

Never run a bundled third-party script. Vendored skills are prompt-only unless the
registry row's "what was stripped" column says otherwise; capability re-introduced
through our own vetted adapter only (see `vetting-protocol.md`).

On any upstream version bump, the skill is **frozen** until sec re-vets and updates
the pin. Loaded artifact must equal the registry-pinned, re-vetted commit.

This is enforced in CI (`scripts/check_skill_registry.py`, wired into the
done-gate): the build fails on unregistered use or provenance drift. The rule
here is the runtime counterpart — follow it even when CI is not in the loop.
```

---

## How it ties together (for super's awareness)

- **Source of truth:** `docs/skills/registry.md` (sec-owned). Status values: `REGISTERED — IN USE` (loadable) · `ELIGIBLE` (vetted, not adopted) · `HELD` · `REJECTED`.
- **CI check** (`scripts/check_skill_registry.py`, run by `scripts/done_gate.py` → CI done-gate job) fails the build when:
  1. a skill bundle (`skills/*/SKILL.md`, `engine/skills/*/SKILL.md`) has **no** registry row;
  2. its row is `REJECTED`/`HELD` but the bundle is vendored;
  3. its row has no real 40-hex pin (placeholder);
  4. the bundle's `SKILL.md` `pinned:` ≠ the registry pin (**drift**);
  5. a `REGISTERED — IN USE` row lacks an on-disk bundle or operator adoption.
- **Current live skills:** `human-tone` (1mk.3) and `brand-voice` (1mk.2) — both `REGISTERED — IN USE`, pins stamped, check green.
- **Known limitation / follow-up:** the drift check compares the registry pin to the bundle's self-declared `pinned:` (offline). Full upstream-tree verification (fetch the pinned commit, diff our vendored copy) needs network and is a documented future enhancement; the re-vet-on-bump policy covers the gap operationally. A `--strict-pins` flag upgrades "bundle declares no pin" from WARN to FAIL once every bundle carries the field (both current bundles already do).
