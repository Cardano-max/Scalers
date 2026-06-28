# brand-voice skill bundle

The **core** writing skill: it makes every tenant-facing draft start from the
artist's real voice instead of generic AI. Everything that writes (copywriter,
reply, outreach) builds on the brand context it assembles. Bead: `CustomerAcq-1mk.2`.

## Contents

```
skills/brand-voice/
  SKILL.md                       # the reusable skill (the technique + recipe + edge cases)
  NOTICE                         # Apache-2.0 attribution to upstream brand-guidelines
  README.md                      # this file
  registry-entry.md              # row for docs/skills/registry.md (sec-owned, 1mk.1)
  references/
    brand-dna.template.md        # per-tenant DNA schema — copy + fill per artist
  tenants/
    ink-studio/
      brand-dna.md               # seed tenant: Ink & Iron (Mara Vance) real DNA
      examples.jsonl             # on-voice few-shots + off-voice negatives
  verify/
    resolve_brand_voice.py       # reference resolver (pack ref -> brand-voice context)
    demo_brand_grounding.py      # demonstration: generic baseline -> grounded prompt
    VERIFICATION.md              # captured evidence + how to reproduce
```

## How a tenant wires it up

The per-tenant pack (`engine/config/packs/<tenant>.toml`) already names the voice:

```toml
[voice]
skill = "brand-voice/<tenant>"
examples_uri = "minio://voice/<tenant>/examples.jsonl"
```

`brand-voice/<tenant>` resolves to **this shared skill + that tenant's DNA**
(`tenants/<tenant>/brand-dna.md`) + on-voice few-shots. `examples.jsonl` here is
the **canonical seed** for `examples_uri`; ops/eng load it to MinIO / the KB.

## Onboarding a new artist

1. `cp references/brand-dna.template.md tenants/<tenant>/brand-dna.md` and fill
   every section with the artist's real data (never invent — leave `TODO(owner)`).
2. Add `tenants/<tenant>/examples.jsonl` (on-voice grounding + a few off-voice
   negatives). Keep `split: "grounding"` examples **disjoint from the eval
   holdout** (`rvy.4`) — grounding examples must not appear in the holdout split,
   or the brand-voice gate measures on training data.
3. Set the pack's `[voice].skill = "brand-voice/<tenant>"`.
4. Run `python skills/brand-voice/verify/demo_brand_grounding.py` adapted to the
   tenant to confirm grounding assembles.

## Status (gated — NOT yet in use)

This bundle is **authored and demonstrated**, but per the HARD RULE (`1mk.1`) it is
**not registered for any agent** until both gates pass:

| Gate | Owner | Status |
|---|---|---|
| Supply-chain vetting + sec sign-off | sec (`1mk.1`) | **PENDING** — prompt-only, nothing to strip; awaiting sec row in `docs/skills/registry.md` |
| Eval gold-set: brand-voice ≥90% on the `rvy.4` holdout (κ≥0.6) | eval (`rvy.4`/`rvy.8`) | **BLOCKED** — `rvy.4` holdout not built yet |

See `registry-entry.md` for the row to merge into the registry once `docs/skills/`
lands (sec PR #25). Provenance + license: see `NOTICE`.

## Follow-ups (file as beads)

- **eng:** wire the resolver contract (`verify/resolve_brand_voice.py`) into the
  engine's on-demand skill load so cells receive the assembled brand-voice context
  (the schema/pack seam already exists; this is the loader that fills it).
- **eval (`rvy.4`):** build the holdout, then run the brand-voice gate; flip the
  eval-gate row to PASS/FAIL.
- **sec (`1mk.1`):** record the registry row + sign-off; flip eligibility to green.
