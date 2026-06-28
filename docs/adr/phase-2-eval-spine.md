# ADR: Phase-2 Eval Spine Architecture

- **Status:** Accepted (pending named-reviewer sign-off — arch + operator at completion)
- **Date:** 2026-06-28
- **Owner:** arch
- **Bead:** CustomerAcq-rvy.1 — dependency-root for all of Phase 2 (blocks rvy.2 KB store, rvy.3 labeling protocol; .7/.8 build to it)
- **Aligns to:** [`docs/systemdesign.md`](../systemdesign.md) §7 + §5.1, [`docs/stack-decision.md`](../stack-decision.md), [`docs/spec.md`](../spec.md) §4 + §5
- **Supersedes:** the Phase-1 promptfoo stub (`evals/promptfooconfig.yaml`) as the *gating harness* — see Decision 4.

This ADR fixes the eval architecture before any Phase-2 code lands, so the gold-set format, the Inspect AI task interface, the storage shape, and the DeepEval gate wiring are built to **one** contract, not three incompatible ones. Phase 1 proved ADR-first prevents rework; the eval spine touches storage (pgvector), CI (GitHub Actions), and three engines with different label types, so the cost of divergence here is high. Pure decision doc — **no implementation**.

---

## Context

The measurable bar (spec §5): brand-voice **≥90%** on-voice on a blind 100-post holdout (≥2 raters, Cohen's **κ ≥ 0.6**); classify/extract precision & recall **≥0.95**; reply safety **0** red-team violations and **<15%** of auto-drafts need editing; validator typed-output **≥99%** after retry; calibration **ECE ≤ 0.05**; email complaints **<0.10%**. The senior-ML critique gate is **"gold set before scaling"** — all calibration and eval-on-every-change depend on a real gold set existing first.

What already exists in the repo (Phase 1) that this ADR builds on, not around:

- `engine/harness/config.py` — `ModelPins` (opus/sonnet/haiku pinned to exact stack-decision strings), `Settings` with temp-0 enforced at load (HARN-06). **Model pins are already authoritative here.**
- `engine/observability.py` — `get_langfuse()` returns `None` when unconfigured (no keys / SDK absent); `is_configured()`; reads `LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST`. **The no-op-when-down property the gate needs already exists.**
- `infra/docker-compose.langfuse.yml` + `infra/.env.example` — self-hosted Langfuse v3 (ClickHouse-backed), localhost:3000, no AWS.
- `scripts/done_gate.py` — the single "done means green" check; gates return PASS/FAIL/**SKIP**/WARN and **skip (stay green) when a subproject isn't scaffolded**; the eval gate is opt-in behind `EVAL_GATE=1`.
- `evals/promptfooconfig.yaml` — a non-blocking promptfoo **stub** wired into that seam.
- `engine/cells/` — `Cell` (typed, schema-validated output; raw model text never flows downstream, §6.3), `ValidatorBank`, `metrics.py`.
- `kb_chunks` table (systemdesign §5.1): `tenant_id`, `kind`, `content`, `embedding vector`, `metrics`(jsonb) — the KNOW-01 pgvector KB.

Tool tension to resolve: backend-plan §3 names **Inspect AI** (harness) + **DeepEval** + Arize **Phoenix** (observability); stack-decision.md names **Langfuse** (observability) + "promptfoo *or* DeepEval" (gating) and is **canonical**. This ADR resolves both (Decisions 4 + 6).

---

## Decision 1 — Gold-example schema (common + per-engine)

**Three tables**, all `tenant_id`-scoped. Separating the *item* from its *labels* is what makes κ computable and re-labeling non-destructive.

### `gold_example` — the item under test (one row per example)

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid PK | |
| `tenant_id` | text NOT NULL → `tenants(id)` | per-tenant isolation; every query filters on it |
| `engine` | enum `POSTING\|OUTREACH\|ENGAGEMENT\|RESEARCH` | engine-agnostic storage — authorable before the engine exists in code |
| `cell` | text NOT NULL | which cell/task this targets, e.g. `content_brief`, `triage`, `personalization`; maps 1:1 to an Inspect `Task` |
| `input` | jsonb NOT NULL | the `CellInput` shape the cell consumes |
| `expected` | jsonb NULL | consensus/canonical label payload (per-engine shape below); may be derived from `gold_label` rows or authored directly |
| `rubric_dimensions` | text[] | e.g. `{voice,safety,appropriateness}` |
| `split` | enum `CALIBRATION\|HOLDOUT\|SMOKE` | HOLDOUT = the blind set for brand-voice ≥90% (never used to tune) |
| `label_version` | int NOT NULL | bumped on any rubric/relabel change (see edge cases) |
| `embedding` | vector NULL | same embed model/dim as the KNOW-01 KB; nullable so labels can be authored before embeddings are backfilled |
| `created_at` / `created_by` | timestamptz / text | provenance |

### `gold_label` — per-rater labels (one row per rater × dimension)

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid PK | |
| `example_id` | uuid → `gold_example(id)` | |
| `tenant_id` | text NOT NULL | denormalized for tenant-isolated queries |
| `rater_id` | text NOT NULL | human or model juror id; `≥2` distinct raters required for HOLDOUT |
| `dimension` | text NOT NULL | one of the example's `rubric_dimensions` |
| `label` | jsonb NOT NULL | per-engine payload (below) |
| `label_version` | int NOT NULL | the rubric version this label was made against |
| `created_at` | timestamptz | |

Store **per-rater rows, never a collapsed value** — agreement (κ / % agreement) is computed from them, so a relabel adds rows rather than overwriting.

### Per-engine `label` / `expected` payloads (the jsonb shape)

```jsonc
// POSTING (on-voice + rubric scores + notes)
{ "on_voice": true, "voice_notes": "matches studio's dry humor",
  "scores": { "voice": 0.92, "safety": 1.0, "appropriateness": 0.95 } }

// OUTREACH (personalization + extracted fields)
{ "on_voice": true, "personalization_score": 0.83,
  "extracted": { "company": "Bayside PG", "role": "Portfolio Mgr", "hook": "new-build vacancy" } }

// ENGAGEMENT (triage class + safety label + appropriateness)
{ "triage_class": "QUESTION_PRICING", "safety_label": "SAFE",
  "reply_appropriate": true }
```

**Rationale.** Common envelope (id/tenant/engine/cell/input/rubric/version/raters/created_at) gives every engine one ingestion + query path; the per-engine bit is an opaque jsonb `label` so the schema is stable while engines differ. Engine-agnostic + jsonb labels satisfy the edge case "label engines that don't exist in code yet" (outreach/engagement land Phase 7). `split=HOLDOUT` + per-rater rows give the blind-holdout + κ machinery spec §5 demands.

---

## Decision 2 — Storage: examples + metrics in the pgvector KB, queryable per tenant

The eval store is the **KNOW-01 KB's offline partition**, in the same Postgres+pgvector instance as the runtime state but **disjoint from the runtime status store** (`runs`/`actions`/`feed_events`, §5.1). The engine harness never reads/writes eval tables on the hot path; they are written by the labeling protocol (rvy.3) and the eval runner, and read by the CI gate and the console's quality views later.

- `gold_example.embedding` is a pgvector column (HNSW/ivfflat index), so examples are retrievable by similarity — the same KB that grounds brand-voice few-shot (KNOW-02) reuses them. rvy.2 picks the index + embed dim to match the KB's existing `kb_chunks.embedding`.
- **`eval_metric`** — the metrics store and the **gating source of truth**:

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid PK | |
| `scope` | enum `TENANT\|GLOBAL` | brand-voice is per-tenant; classifier P/R may be engine-global |
| `tenant_id` | text NULL | NOT NULL when `scope=TENANT` (CHECK); every tenant-scoped query filters on it |
| `engine` / `cell` | text | |
| `metric` | text | `ece`, `precision`, `recall`, `f1`, `brand_voice_onvoice`, `kappa`, `validator_pass`, `edit_rate`, … |
| `value` | double | |
| `threshold` | double | the bar from spec §5 (see Decision 4) |
| `direction` | enum `GTE\|LTE` | ECE is LTE; the rest GTE |
| `passed` | bool | computed `value`⨝`direction`⨝`threshold` |
| `run_kind` | enum `PER_COMMIT\|PER_PROMOTION` | which gate produced it |
| `label_version` | int | the gold version it was computed against |
| `model_pins_hash` | text | hash of `ModelPins` (Decision 6) |
| `prompt_version` | text | prompt hash/version (Decision 6) |
| `dataset_hash` | text | hash of the example set used |
| `git_sha` | text | commit under eval |
| `langfuse_trace_id` | text NULL | best-effort cross-link (Decision 6); never required |
| `created_at` | timestamptz | |

**Per-tenant isolation (edge case):** `tenant_id` on both `gold_*` and `eval_metric`; the data-access layer requires a `tenant_id` (or explicit `scope=GLOBAL`) on every read — no query returns cross-tenant rows. **Versioning (edge case):** an `eval_metric` row records the `label_version`, `model_pins_hash`, `prompt_version`, `dataset_hash` it was computed under, so a relabel or bump never silently invalidates an old metric — it produces a *new* identity (Decision 6) and the old row stays as history.

**Rationale.** One authoritative, deterministic, queryable-in-SQL store for gating; the vector column keeps examples first-class KB citizens for grounding; disjoint-from-runtime keeps the eval governance load off the engine's hot path.

---

## Decision 3 — Eval harness: the Inspect AI Task / dataset / solver / scorer boundary

**Inspect AI is the canonical eval harness.** Each cell-under-test is one Inspect `Task`. A cell plugs in through three adapters (live in `evals/inspect_tasks/`), so adding an engine in Phase 7 = adding a task file, not touching the harness:

```
dataset_for(engine, cell, tenant, split) -> Dataset      # gold_example rows -> Inspect Samples
    Sample(input=row.input, target=row.expected,
           metadata={tenant_id, label_version, rater_agreement, dataset_hash})

cell_solver(cell, *, live: bool) -> Solver                # invokes the Cell under test
    live=False (PER_COMMIT): Inspect mock model (mockllm / FunctionModel) replays
                 recorded outputs — runs offline, no API keys, deterministic
    live=True  (PER_PROMOTION): the real pinned model via Pydantic-AI, temp-0

scorers_for(cell) -> list[Scorer]                         # DeepEval-backed scorers
    classification: precision / recall / f1 vs target.label
    calibration:    ECE / Brier over (confidence, correct) pairs   (Decision 5)
    voice:          on-voice rate vs human/jury labels + κ across raters
    validator:      typed-output pass-rate after retry (pure code, no model)
```

- The scorer reads the cell's **typed output object**, never raw model text (consistent with §6.3) — so a parse failure is itself a recorded outcome, not a scorer crash.
- **DeepEval** supplies the calibration/classification metric implementations (ECE/Brier native); they are wrapped as Inspect `@scorer`s so there is **one** runner. RAGAS (research RAG) is deferred to Phase 7.
- **Offline vs live is the per-commit/per-promotion seam** (Decision 4): per-commit runs `live=False` against recorded fixtures + pure-code/DeepEval recompute (hermetic, key-free); per-promotion runs `live=True` against pinned models + human/jury labels.

**Rationale.** Inspect's dataset→solver→scorer model is exactly our cell-eval shape and the bead mandates it; mock-model solvers make the per-commit gate hermetic (no keys, deterministic) while the same task definition runs live for promotion — one task, two model backends.

---

## Decision 4 — Threshold → CI pass/fail, and the per-commit vs per-promotion split

Every threshold becomes an `eval_metric` row with a `passed` bool; **the CI gate is "all *required* metrics present and passed; metrics with no gold data are SKIP (neutral), not FAIL."** Thresholds + direction live in one registry (`evals/thresholds.yaml`) so .7/.8 add rows without touching gate code.

| Metric | Threshold (spec §5) | Dir | Gate | Phase-2 active? |
|--------|--------------------|-----|------|-----------------|
| validator typed-output after retry | ≥0.99 | GTE | **per-commit** (pure code) | yes |
| router determinism | exact | — | **per-commit** (pure code) | yes |
| classify/extract precision, recall, F1 | ≥0.95 | GTE | **per-commit** offline (mock + recorded) → re-confirmed **per-promotion** live | yes |
| calibration ECE | ≤0.05 | LTE | **per-commit** offline (recompute over stored preds) → **per-promotion** live | yes |
| brand-voice on-voice (blind holdout) | ≥0.90 | GTE | **per-promotion** (human/jury raters) | yes |
| rater agreement κ (holdout) | ≥0.6 | GTE | **per-promotion** | yes |
| auto-draft edit rate | <0.15 | LTE | **per-promotion** (live + human) | scaffold; data in P5/P7 |
| reply safety red-team violations | 0 | LTE | **per-promotion** (live) | scaffold; P5 |
| email complaints | <0.0010 | LTE | runtime, not CI | P6/P7 |

- **Per-commit gate** (runs on every PR, inside `scripts/done_gate.py`): hermetic, offline, no model keys. It executes the Inspect tasks with `live=False`, writes `eval_metric` rows (`run_kind=PER_COMMIT`), and **fails the build if any required row `passed=false`**. This replaces the promptfoo stub at the *same* `EVAL_GATE` seam — keep the seam, swap promptfoo → `inspect eval` (promptfoo retired to avoid a third gating tool). Once a gold set exists for a cell, its per-commit gate flips from opt-in to **mandatory** for that cell.
- **Per-promotion gate** (runs on a `release`/`promote` workflow or the `eval-full` PR label, **not** every commit): live pinned models, human/jury raters, red-team. Gates promotion of an autonomy dial / a model-or-prompt bump.
- **Skippable/neutral (edge case):** if `gold_example` has zero rows for `(engine, cell)` — e.g. outreach/engagement in Phase 2 — the task yields **SKIP**, mirroring the existing done-gate graceful-skip. No false build failure for an unbuilt engine. A cell with gold data but a missing required metric is a **FAIL** (data exists, gate must run).

**Rationale.** Cheap deterministic checks gate every commit; expensive human/live checks gate promotion. The gate's data source is `eval_metric` (SQL), so "green" is reproducible and independent of any live service (Decision 6). One thresholds registry + the SKIP-when-no-data rule lets the same gate code serve Phase-2 (one engine) through Phase-8 (all engines) unchanged.

---

## Decision 5 — Confidence = self-consistency variance (no logprobs)

**Confirmed (per stack-decision.md + spec §5 + systemdesign §6/§7):** hosted Claude/Gemini expose **no logprobs/activations**, so the confidence signal the router consumes and the calibration target (ECE ≤0.05) optimizes is **self-consistency variance**, not token-margin/logprob methods.

- The confidence computer is a **separate probe** from the temp-0 decision path: it samples the generating/judging cell **K times with varied prompts/seeds**, then scores agreement/variance of the typed outputs → a computed confidence in `[0,1]`. The decision/classify cells themselves stay temp-0 and pinned (HARN-06); the probe's sampling does not change the routed decision, only its confidence.
- Calibration (ECE/Brier) is computed over `(self_consistency_confidence, correct_bool)` pairs against the gold set — the `eval_metric` `ece` row's input contract.
- **Reserve token-margin/linear-probe methods for self-hosted cells only** (the local Ollama cross-family juror), where logits are available — not for any hosted-Claude cell. This is the documented "confidence-signal availability" critique fix.

**Rationale.** It is the only calibration input actually implementable on the chosen hosted models; baking the `(confidence, correct)` contract into `eval_metric` now means Phase-5 autonomy calibration plugs in without a schema change.

---

## Decision 6 — Observability: Langfuse self-hosted is canonical; gating is independent of it

**Langfuse self-hosted (v3, ClickHouse-backed) is canonical** for trace capture, the prompt/version registry, and eval-result trend/visualization. This **resolves the tool tension**: backend-plan §3 named Arize Phoenix; **stack-decision.md wins → Langfuse** (the systemdesign §7 cross-ref). It runs in the local Docker stack (`infra/docker-compose.langfuse.yml`, localhost:3000), no AWS.

**The separation that satisfies "the CI gate must NOT hard-depend on a live Langfuse":**

| Concern | Source of truth | Role |
|---------|-----------------|------|
| **Gating** (pass/fail) | **`eval_metric` (Postgres KB)** | authoritative, deterministic, queried by `done_gate.py` |
| **Observability** (traces, trends, prompt versions) | **Langfuse** | best-effort, observational, **never gates** |

- The eval runner writes the authoritative metric to `eval_metric` **and** best-effort-mirrors it to Langfuse (as scores on the run/trace), tagged with the same `git_sha`/`dataset_hash`/`model_pins_hash`/`prompt_version`, storing the returned `langfuse_trace_id` back on the `eval_metric` row for cross-linking. **If Langfuse is down, the mirror is skipped and the gate still passes/fails correctly** — this is already true structurally: `observability.get_langfuse()` returns `None` when unconfigured, so the engine, tests, eval runner, and CI all run without a live server. Tracing is best-effort; gating is authoritative. (Edge case satisfied.)

**Version pinning + re-eval on bump (stack-decision "pin model versions and re-run evals on every bump") — enforced structurally, not by discipline:**

- Model pins come from `engine/harness/config.py:ModelPins` (already pinned to exact stack-decision strings); their hash = `model_pins_hash`.
- Prompt versions are registered in the Langfuse prompt registry **and** captured as `prompt_version` on each `eval_metric` row.
- An eval result's **identity = (cell, dataset_hash, model_pins_hash, prompt_version)**. A model or prompt bump changes the hash → the gate finds **no passing row for the new identity** → re-eval is **forced** before promotion. The bump cannot pass on a stale metric.

**Phase-2 observability scope (so .7/.8 build to it and no separate observability bead is needed to unblock the gate):**

- **Wired in Phase 2:** (1) trace capture for eval runs + engine cells (via `observability.py`, already scaffolded); (2) the authoritative `eval_metric` store + best-effort Langfuse eval-result mirror; (3) `model_pins_hash` + `prompt_version` capture on every metric row.
- **Deferred:** moving prompt *text management* into the Langfuse registry UI (Phase 2 only needs `prompt_version` as a hash + git); runtime per-action trace sampling / the 5–10% human cross-check → **Phase 5** (autonomy); RAGAS research-RAG scorers → **Phase 7**.

**Rationale.** One canonical observability tool (no Phoenix/Langfuse split); a hard wall between *observing* and *gating* so CI is reproducible offline; and a hash-identity for eval results that turns "re-eval on every bump" from a rule people forget into a property the gate enforces.

---

## Consequences

- **Buildable now without new design questions:** rvy.2 builds the `gold_example`/`gold_label`/`eval_metric` tables (Decisions 1–2) + the tenant-isolated DAL; rvy.3 authors the labeling protocol/rubric/format against this schema (per-rater rows, `split`, `label_version`); .7/.8 add an Inspect task file + thresholds-registry rows per new cell (Decisions 3–4) — the harness, gate, and store do not change.
- **The Phase-1 promptfoo stub is retired** at completion of rvy-eval-harness work; the `EVAL_GATE` seam in `done_gate.py` stays and runs `inspect eval` offline. Until a cell has gold data, its gate is SKIP-neutral, so CI stays green through the transition.
- **The gold set is the true blocker** (rvy.3): every per-commit metric except validator/router needs recorded predictions + labels; the gate is honest about emptiness (SKIP), so Phase-2 can land the spine before the gold set is fully authored, but cannot *promote* until it exists.
- **One thing to watch:** recorded-fixture drift — when a cell's prompt changes, its per-commit recorded outputs must be regenerated, or the offline metric measures stale behavior. The `prompt_version` identity (Decision 6) makes this visible (stale fixtures → no passing row for the new identity), but fixture regeneration is a per-promotion step the labeling protocol (rvy.3) must own.

## References

- `docs/systemdesign.md` §7 (testing strategy: "Langfuse + Inspect/DeepEval CI gate"), §5.1 (`kb_chunks`, status store), §6.3 (typed cells / validator bank)
- `docs/stack-decision.md` (Langfuse canonical; promptfoo/DeepEval gating; pin + re-eval on bump; self-consistency confidence; local-Ollama cross-family juror)
- `docs/spec.md` §4 (data model), §5 (measurable targets)
- Repo: `engine/harness/config.py` (`ModelPins`, temp-0), `engine/observability.py` (`get_langfuse()` no-op fallback), `scripts/done_gate.py` (`EVAL_GATE` seam, SKIP semantics), `infra/docker-compose.langfuse.yml`, `evals/promptfooconfig.yaml` (superseded)
