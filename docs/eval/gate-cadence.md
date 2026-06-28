# Eval-gate cadence — per-commit vs per-promotion (the honest cadence)

> Exit doc for the Phase-2 eval spine (rvy.9). An eval gate never observed failing
> is indistinguishable from no gate — this records *which* gates run *when*, so no
> one assumes the human-rated brand-voice gate runs on every push.

The threshold registry is `engine/evals/gate.py` (`GATES`) — the executable source
of truth, mirroring ADR Decision 4 (`docs/adr/phase-2-eval-spine.md`). Every
threshold becomes an `eval_metric` row with a computed `passed`; that table is the
**durable gating source of truth**, queryable per tenant.

## Per-commit (every PR) — hermetic, offline, no model keys

Runs inside `scripts/done_gate.py` at the `EVAL_GATE` seam (`evals/run_gate.py`),
`live=False` over recorded fixtures / the smoke set — deterministic, reproducible.
Fails the build if any **required** metric regressed; **SKIP (neutral)** when a
gold set is absent (never a false fail or a silent pass).

| Metric | Bar | Dir | Engine·cell |
|---|---|---|---|
| classify recall (triage) | ≥0.95 | GTE | engagement·triage |
| reply-safety recall (`must-escalate`) | ≥0.95 | GTE | engagement·triage |
| extraction exact-match | ≥0.95 | GTE | outreach·prospect_extract |
| calibration ECE *(rvy.8)* | ≤0.05 | LTE | (rvy.8 owns the confidence source) |
| validator typed-output, router determinism *(pure code, rvy.7)* | ≥0.99 / exact | — | harness |

## Per-promotion (release / `eval-full` label / autonomy-dial lift) — live, human/jury

NOT every commit. Live pinned models + human/jury raters + red-team. Gates the
promotion of an autonomy dial or a model/prompt bump (the **439 lift**).

| Metric | Bar | Dir | Why per-promotion |
|---|---|---|---|
| brand-voice on-voice (blind holdout) | ≥0.90 | GTE | needs ≥2 human raters |
| rater agreement κ (holdout) | ≥0.6 | GTE | human inter-rater |
| auto-draft edit rate | <0.15 | LTE | live + human (P5/P7) |
| reply-safety red-team violations | 0 | LTE | live red-team (P5) |

## Non-negotiables

- **SMOKE never lifts a real gate.** The `split=smoke` set (rvy.10, test tenant
  `ladies8391`) proves the machinery runs; a smoke pass is never reportable as a
  real brand-voice / P-R / ECE result and does **not** satisfy the 439 autonomy
  hold. Real measurement uses `split=holdout/train` on the real client tenant.
- **Per-channel 439 lift** is attested by QA (qa1) per channel: a `PASS` verdict
  bead comment naming the channel + metrics + gold-set version (`label_version` +
  `dataset_hash`), backed by `eval_metric` rows. Each channel lifts independently.
- **Determinism:** the per-commit verdict is reproducible (no LLM in the
  per-commit path); the same commit evaluated twice yields the same pass/fail.
