# ADR: Phase-5 Autonomy Engine

- **Status:** Accepted (build contract for 4jx.2–.9; arch + operator sign-off at completion, like a9m.1)
- **Date:** 2026-06-28 · **Owner:** arch · **Bead:** CustomerAcq-4jx.1 (epic 4jx — the real autonomy engine)
- **Builds on:** `b3f` (`AutonomyMode.HOLD` + fail-safe `HoldRegistry`, merged), `a9m.1` Decision 4 (unified `router.route` precedence), `rvy.1` (eval spine: Decision 5 self-consistency confidence, Decision 6 Langfuse-mirror/authoritative-store), `docs/stack-decision.md`, the `kkg.2` persistence schema, and the eval gates `rvy.7`/`rvy.8`.
- **Composes with (in flight):** `4z2` (two-layer HOLD: router + independent `SideEffectBoundary` hold gate), `4jx.2`'s hard-fail floor + pmm/sec rubrics.
- **Blocks:** 4jx.2 (jury), .3 (confidence), .4 (embedder), .5 (gates), .6 (safety classifier), .7 (dial), .8 (439-lift), .9 (integration proof).

The autonomy engine is the highest-stakes subsystem — it decides what auto-fires. This ADR fixes how the **real jury**, **computed confidence**, **deterministic gates**, **independent safety classifier**, **per-channel dial**, and the **439-lift state machine** compose on top of b3f's HOLD primitive, so the four stub replacements + the lift wiring build to **one** contract. Pure decision doc — no implementation.

---

## Context — four stubs + a lift, on top of HOLD

The b3f audit flagged four stubs that must not gate an auto-fire: the **jury** (every judge gets the same base confidence → agreement always 1.0, no model called), **confidence** (hardcoded 0.9), the **embedder** (SHA-256, not semantic), and the **gold set** (mock). b3f's response: the system is **HELD by default** — nothing auto-fires until each is real *and* an operator lift is recorded. Phase 5 replaces the four stubs and wires the lift.

Two pure functions already exist and are the foundation this ADR extends:
- **`harness/router.route()`** — the control valve. Precedence is pinned by a9m.1 Decision 4: escalate-gate→review · regenerate-gate→regenerate · **HELD→review** · conf<thr→review · dial REVIEW→review · auto.
- **`autonomy/decision.derive_decision()`** — the autonomy layer over `route()`. It pools the jury, computes agreement, and applies the autonomy-only blockers `route()` can't see (safety veto, jury split, degraded coverage), producing the persisted `DecisionRecord`.

### The composition invariant (the safety spine of this ADR)

**The autonomy layer is monotonic toward review: it can only downgrade `route()`'s decision (auto→review/regenerate), never upgrade it (review→auto).** `derive_decision` returns AUTO **only if** `route()` returned AUTO (⇒ not held, not gated, conf≥thr, dial=auto) **and** no autonomy blocker (safety/split/degraded/hard-fail) fired. Therefore **HOLD always wins** (b3f invariant, 4jx.1 edge case): a held channel's `route()` returns REVIEW, so no jury score, confidence, or dial setting anywhere in the autonomy layer can produce AUTO. Every new signal below is a *blocker*, never an *enabler*.

---

## Decision 1 — Jury (real, cross-family, per-dimension, hard-fail floor)

**Judge set (no external-key assumption).** Independent **Claude Opus 4.8 jurors** with *varied prompts* at temp-0 (N≥2, distinct rubric framings) **+ ≥1 local open model via Ollama** as the cross-family juror. GPT-5.5 / Gemini / DeepSeek jurors are included **only if** their keys are provided. **Edge: only the Anthropic key present → cross-family is still satisfied by the Ollama juror** — the engine must never silently collapse to single-family.

**Per-dimension, independent scoring.** Each judge is a typed cell (no raw text) emitting, **per dimension** — `voice`, `safety`, `appropriateness` — a `[0,1]` score (0–4 anchor scale normalized) + an `on_voice` bool + a `hard_fail: bool` (tagged disqualifier). **Dimensions are scored independently and never collapsed** (load-bearing, pmm): a post can be in the exact artist voice yet inappropriate (e.g. framing a client's mastectomy scar as a "before/after glow-up" → voice ≈4, appropriateness ≈1). A low score on any one dimension sinks the action independent of the others.

**Rubrics (cited as-is, owned outside arch).** Voice + appropriateness: `pmm/positioning/jury-rubric-voice-appropriateness.md` (0–4 anchors→[0,1], hard-fails, band exemplars, per-artist extension from the brand-dna "Sensitive subjects / out-of-scope" block). Safety: **sec-owned** rubric. **One rubric drives both the human raters and the jurors** so they score identically (anchors = writer's canonical corpus, `split=rubric`). Human-rated brand-voice ≥0.90 / κ≥0.6 is measured on the rvy.4 holdout by the rvy.3 rater — not by the jury.

**HARD-FAIL = deterministic floor (engine guarantee, 4jx.2).** A rubric **hard-fail** on any dimension is a *disqualifier*, not a low number that weighted averaging can wash out — a hard appropriateness/safety fail can **never** be averaged away by a high voice score. Hard-fails must be **machine-detectable (tagged)**, and the aggregator checks them **before** the weighted mean: **any hard-fail → the action cannot AUTO** (force escalate/regenerate). This is a floor in the same family as the escalate-gate (a9m.10) and the safety veto (Decision 3).

**Deterministic reliability-weighted aggregation (scalar; CollabEval deferred, critique #4).** Pure code, per dimension:
- `dim_score[d] = Σ(w_j · score_j[d]) / Σ w_j` over judges j, where `w_j` = a reliability weight (default uniform; calibrated against the gold set when available — pinned, reproducible).
- **`hard_fail[d] = any(judge.hard_fail on d)`** — computed separately, not folded into the mean.
- **Real agreement, per dimension:** `agreement[d] = 1 − spread(score_j[d])` (spread = max−min, or variance-based), **computed from the judges' actual divergence — never hardcoded**. The decision uses the **worst** dimension's agreement (a split on *any* dimension is a split). With the real jury, scores diverge, so agreement is meaningful (the stub's constant-0.9 → 1.0 artifact is gone).

**Edge cases:** a judge times out/errors/refuses → **drop it with reduced weight; never block the run or fabricate agreement**. All judges unavailable → **fail safe (no confidence → review)**, never a default passing score. Judges unanimously-wrong-but-agreeing → that's why the **independent** safety classifier (Decision 3) backstops the safety dimension.

---

## Decision 2 — Confidence (self-consistency, no logprobs; pooled; calibrated)

**Self-consistency (rvy.1 D5).** Hosted Claude exposes no logprobs, so generation confidence = **self-consistency variance**: sample the producing cell **K times** with varied prompts/seeds (a temp>0 *probe*, separate from the temp-0 decision path), and score the agreement/variance of the typed outputs → `self_consistency ∈ [0,1]`. Reserve token-margin methods for self-hosted/Ollama cells only.

**Pooling into one confidence.** The router consumes a single `confidence`:
```
confidence = calibrate( w_q · jury_quality + w_c · self_consistency )
```
where `jury_quality` = the reliability-weighted pooled jury score (Decision 1) and `self_consistency` = generation stability. Weights `w_q,w_c` + the calibration map are **fit on the gold set** (rvy.8). The two are conceptually distinct (jury = *is it good?*, self-consistency = *is the generator stable?*); both must be high to trust an auto-fire, so pooling is **conservative** (a low self-consistency pulls confidence down even if the jury liked the one sample it saw).

**Calibration (rvy.8).** Thresholds are calibrated on the **real per-channel gold set** so the routed `confidence` is honest: **ECE ≤ 0.05** computed over `(confidence, correct)` pairs (the rvy.8 gate, which goes *live* on real confidence exactly here in Phase 5 — Phase 2 exercised it on stand-in data). The router threshold is per-channel config (Decision 5 dial).

**Edge: confidence uncomputable** (insufficient samples / probe failure) → **fail safe to review**; never treat "couldn't compute" as high confidence.

---

## Decision 3 — Deterministic gates + independent safety classifier

**Deterministic gate set (4jx.5).** Pure-code gates producing `list[Gate]` (the existing `Gate{name, passed, detail, on_fail}` shape, `on_fail` per the a9m.10 disposition model): **suppression, rate cap, PII redaction, tenant policy, media format** (+ the a9m.1 content gates: banned/claim/`sensitive_ban`/media-spec). No model call. Read by `route()` (rules 1–2).

**Independent safety classifier (4jx.6).** A **separate model and code path from the jury** — *not* a juror — emitting `SafetyVerdict ∈ {PASS, FLAG, VETO}`. **VETO = hard block (never auto, force review); FLAG = escalate.** Independence is the point: a jury blind spot (all jurors agree but are wrong) must not also blind safety, so the safety classifier uses a different model/family and runs on its own path. It **backstops** the jury's `safety` dimension and the rubric hard-fails — defense in depth, not redundancy.

**The deterministic floors, together:** escalate-gate (a9m.10) + rubric hard-fail (Decision 1) + safety VETO (here) are three independent disqualifiers, each of which **alone** prevents AUTO regardless of confidence or jury average. None can be averaged out.

---

## Decision 4 — Decision precedence + `EscKind.HELD`

Two layers, both pinned:

**`route()`** — unchanged from a9m.1 D4 (escalate-gate · regenerate-gate · HELD · conf · dial · auto). Implemented by b3f.

**`derive_decision()`** — wraps `route()` and adds the autonomy-only blockers. Canonical **escalation-reason precedence** (first match wins; all but the last → not-AUTO):

| # | Condition | decision | esc.kind |
|---|-----------|----------|----------|
| 1 | deterministic gate failed (regenerate/escalate) | regenerate / review | `GATE` |
| 2 | safety VETO/FLAG | review | `SAFETY` |
| 3 | rubric **hard-fail** on any dimension | review/regenerate | `GATE` (hard-fail tag) |
| 4 | jury split (worst-dim agreement < `AGREEMENT_MIN`) | review | `SPLIT` |
| 5 | degraded jury (judges < expected) | review | `DEGRADED` |
| 6 | **channel HELD** (439, not lifted) | review | **`HELD`** (new) |
| 7 | confidence < threshold | review | `BELOW_THRESHOLD` |
| 8 | dial = APPROVE-FIRST | review | `MODE` |
| 9 | else | **auto** | `NONE` |

**New `EscKind.HELD`** distinguishes a 439-hold review from a dial approve-first review (`MODE`) in the record + console — today `derive_decision` would mislabel a held channel's review as `MODE`. HELD sits at #6 (a held channel still surfaces a higher-priority gate/safety/hard-fail/split reason if one applies — those are more actionable — but is never AUTO). The monotonic invariant holds: #9 (auto) is reachable only when nothing above fires, which requires not-held.

---

## Decision 5 — Dial + the 439-lift state machine

**States, per (tenant, channel):**
```
HELD ──[rvy.7 green ∧ rvy.8 green on the channel's REAL gold set]──▶ ELIGIBLE
ELIGIBLE ──[operator sets dial]──▶ AUTO  |  APPROVE-FIRST
{AUTO|APPROVE-FIRST} ──[eval/calibration regression OR safety incident]──▶ HELD   (AUTO-REVERT)
```

- **Default HELD** (b3f); **SMOKE gold set never lifts** (smoke is pipeline-only, never a gating/auto source).
- **Per-channel lift condition:** `rvy.7` (Inspect suite green) **AND** `rvy.8` (ECE≤0.05, P/R≥0.95, brand-voice≥0.90 κ≥0.6) **green on that channel's real (non-smoke) gold set**. Lift is **per-channel** (not per-engine) — a channel lifts only when its own evidence passes.
- **Backend-enforced, single source of truth.** Lift state is a **durable** record (the `HoldRegistry` backed by a `autonomy_lifts` table), **not** a UI toggle. A `LiftController` reads the latest `eval_metric` (rvy.8) per channel and records/revokes lifts. The operator dial (AUTO vs APPROVE-FIRST + threshold, 4jx.7) only takes effect **after** lift — a held channel ignores the dial (HOLD wins).
- **AUTO-REVERT on regression (P0, 4jx.8):** if a later eval/calibration run goes red for a channel, or a safety incident fires, the LiftController **re-holds** it automatically (revokes the lift) — lift is conditional and revocable, never sticky.
- **Two-layer enforcement (4z2):** the same single lift-state source is read by **both** the router (`effective_autonomy` → HOLD if held) **and** the independent `SideEffectBoundary` hold gate. Auto-revert updates the one source → both layers re-hold atomically. The send boundary independently refuses a held channel even if a future routing bug yields AUTO.

This makes "lift" a *backend state machine gated on measured evidence*, not a switch — the only way a channel auto-fires is real-gold eval+calibration green, operator dial=AUTO, and no blocker — and it snaps back to HELD the moment the evidence regresses.

---

## Decision 6 — Persistence (kkg.2 mapping + additive Phase-5 delta)

Real jury/decision data maps onto the kkg.2 schema: `autonomy_decisions` (pooled_confidence, threshold, agreement, decision, safety_verdict, esc_kind/label, gates jsonb) + `autonomy_jury` (one row per real judge: judge, family, voice, safety, appr). The real jury simply writes real rows where the stub wrote uniform ones.

**Additive delta Phase-5 needs (flagged — kkg.2 said "no schema change" for the *verdicts*, but these are *new signals* the stub didn't carry; all additive, nullable/defaulted, so existing rows + the console binding keep working):**
- `autonomy_jury`: add `reliability_weight DOUBLE` (the aggregation weight) and `hard_fail BOOLEAN DEFAULT false` (the per-judge, per-dimension disqualifier tag — needs to be queryable, not only prose).
- `autonomy_decisions`: add `self_consistency DOUBLE NULL` (the generation-stability component of confidence) so the console/eval can show both confidence inputs.
- `EscKind`: add `HELD` (Decision 4).
- `autonomy_lifts` (new): `(tenant_id, channel, lifted_at, lifted_by, eval_metric_ref, reverted_at, reverted_reason)` — the durable lift ledger the `LiftController` + both enforcement layers read.

This is a small migration (call it `06-autonomy-phase5.sql`), owned by the persistence bead, additive over kkg.2's `05`-era schema.

---

## Edge cases (consolidated, from 4jx.1)

- **Only an Anthropic key** → cross-family met via the Ollama juror; never collapse to single-family.
- **Judge timeout/error/refusal** → drop with reduced weight; never block or fake agreement.
- **All judges down** → fail safe to review (no confidence), never a passing default.
- **Confidence uncomputable** → fail safe to review.
- **Per-engine vs per-channel lift** → **per-channel** (a channel lifts on its own evidence).
- **HOLD always wins** → guaranteed by the monotonic composition invariant (Decision 4); no jury/confidence/dial path overrides a held channel.
- **Hard-fail vs average** → hard-fail is a separate disqualifier checked before the mean; never averaged out.
- **Regression after lift** → auto-revert to HELD.

---

## Build-bead fan-out (.2–.9) — interfaces each builds to

| Bead | Builds | Key interface (from this ADR) | Owner notes |
|------|--------|-------------------------------|-------------|
| **4jx.2** | Real jury | judge set + per-dimension typed cells + `hard_fail` tags + reliability-weighted aggregation + real per-dim agreement → `list[JudgeVote+]` | rubric = pmm (voice/appr) + sec (safety) |
| **4jx.3** | Confidence | self-consistency probe (K samples) + pooling `calibrate(w_q·jury + w_c·sc)` + ECE≤0.05 on gold | reads rvy.8 calibration |
| **4jx.4** | Semantic embedder | local model → pgvector; replace SHA-256; re-embed KB | enables real voice-similarity + grounding |
| **4jx.5** | Gate set | `list[Gate]` (suppression/rate/PII/tenant/media) w/ `on_fail` disposition | pure code |
| **4jx.6** | Safety classifier | independent model/path → `SafetyVerdict{PASS,FLAG,VETO}`; veto=hard block | separate from jury |
| **4jx.7** | Dial | per-channel AUTO/APPROVE-FIRST + threshold, operator-settable, console-visible | effective only post-lift |
| **4jx.8** | 439-lift wiring (P0) | `LiftController` + `autonomy_lifts` + auto-revert; two-layer (router + 4z2 boundary) read one source | per-channel; real-gold green; SMOKE never lifts |
| **4jx.9** | Integration proof | real jury+confidence+embedder+gates+safety route an action; lift a channel on green; force a regression → auto-revert to HELD | mirrors a9m.9/rvy.9 |

---

## Consequences & references

- A reviewer can build .2–.9 from this with no new design questions; every replacement is a *blocker that can only downgrade toward review*, so swapping stubs for real components can never make the engine **less** safe than b3f's held-by-default baseline.
- The lift is the only path to auto, and it is evidence-gated + auto-reverting + two-layer-enforced — the operator's "no off-brand/unsafe auto-send" property is structural, not procedural.
- **Dependencies surfaced for grooming:** the `autonomy_lifts` table + the additive kkg.2 columns (Decision 6) are a small migration the persistence/`.8` bead owns; the pmm voice/appr rubric + sec safety rubric must exist before `.2` (they are cited, not yet committed); `.8` consumes `rvy.7`/`rvy.8` per-channel results.

*Refs: `b3f` (`harness/hold.py`, `router.py`), `a9m.1` Decision 4, `rvy.1` D5/D6, `autonomy/{decision,jury,store,produce}.py`, `kkg.2` schema (`autonomy_decisions`/`autonomy_jury`), `stack-decision.md`, `4z2`, `4jx.2`.*
