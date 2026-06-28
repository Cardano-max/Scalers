# Skill registry entry — brand-voice

Row to merge into `docs/skills/registry.md` (sec-owned, `1mk.1`). That file lands
via sec PR #25; this fragment carries the values so the row is ready. Per the HARD
RULE, **no registry row with green eligibility → no agent use.** This entry ships
with eligibility **PENDING** and adoption **NONE** by design.

| Field | Value |
|---|---|
| **Skill** | `brand-voice` (per-artist brand voice) |
| **Bead** | `CustomerAcq-1mk.2` |
| **Upstream source** | `github.com/anthropics/skills` — `skills/brand-guidelines` |
| **Pinned commit** | `b9e19e6f44773509fbdd7001d77ff41a49a486c1` (2026-04-20) |
| **Upstream license** | Apache-2.0 (`skills/brand-guidelines/LICENSE.txt`) — redistribution + derivative works permitted; trademarks excluded. See `NOTICE`. |
| **Skill type** | Prompt-only (markdown). No scripts shipped by upstream. |
| **What was stripped/sandboxed** | **Nothing to strip** — upstream is prompt-only markdown; no network/file/exec/credential/money capability. Adopted as a *structure-only* derivative; none of Anthropic's brand content (colors/typography/marks) is reproduced. The only executable files in this bundle (`verify/*.py`) are **our own** stdlib-only resolver + demo, not vendored upstream. |
| **Re-authored to our format** | Yes — `skills/brand-voice/SKILL.md` (+ DNA template, per-tenant DNA, examples). Grounded in `docs/skills/winning-strategies-kb.md`. |
| **Our-format path** | `skills/brand-voice/` |
| **sec sign-off (security)** | **PENDING** — sec to review (prompt-only; injection-surface = the DNA files, which are operator-authored tenant data). |
| **Eval-gate status** | **BLOCKED/PENDING** — gate = brand-voice ≥90% on the `rvy.4` holdout (κ≥0.6); `rvy.4` holdout not built yet (`rvy.8` runs the gate). |
| **Eligibility (gate green?)** | **NO** — pending sec sign-off + eval-gate. |
| **Adoption (operator-approved + assigned)** | **NONE** — not registered for any agent; not IN USE. |
| **Which agent uses it** | (none yet) → intended: posting/reply/outreach writing cells, conditionally loaded by cell type. |

## Provenance check (vetting protocol step 3)

- Pinned to a real 40-hex commit (verified via GitHub API), not an alias name.
- License confirmed Apache-2.0 at that commit; `NOTICE` satisfies the §4
  redistribution conditions (license copy reference, modification notice,
  attribution retained, no trademark use).
- Re-vet required on any upstream bump (new commit → new row).
