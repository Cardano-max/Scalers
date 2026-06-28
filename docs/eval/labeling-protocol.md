# Scalers Gold-Set Labeling Protocol

> Deliverable of bead **CustomerAcq-rvy.3** (Phase 2 — Eval spine & gold set).
> Purpose: make the three engine gold sets reproducible and trustworthy so the eval gates
> (classify/extract P/R ≥0.95, brand-voice ≥90% κ≥0.6, calibration ECE ≤0.05) measure something real.
> Canonical alignment: docs/spec.md §5, docs/stack-decision.md, docs/adr/phase-2-eval-spine.md (gold_example schema).
> Owner: pm (protocol) · per-engine gold-set owners named in §1.

---

## 1. Owner assignment (REQUIRED — observable sign-off)

Every engine gold set has **one accountable owner** for label quality. Assignment is not "done" until it is
**recorded as an observable artifact**: either this sign-off table is filled in (name + date), **or** a sign-off
comment is posted by the operator/owner on bead CustomerAcq-rvy.3. An unsigned row blocks the matching gold-set bead.

| Engine | Gold-set bead | Proposed owner | Operator-approved? (name / date) |
|--------|---------------|----------------|----------------------------------|
| Posting (engine 1) | rvy.4 | _[operator to confirm]_ | ☐ pending sign-off |
| Outreach (engine 2) | rvy.5 | _[operator to confirm]_ | ☐ pending sign-off |
| Engagement (engine 3) | rvy.6 | _[operator to confirm]_ | ☐ pending sign-off |

> Operator ask already filed: "name a gold-set owner per engine." The **real artist brand-voice rater**
> (a human with tattoo-studio voice judgment) is assigned at **client onboarding**; until then the SMOKE
> set (rvy.10) uses internal raters and synthetic labels and does **not** satisfy any real-holdout gate.

---

## 2. Gold_example record format

Labels are authored in the `gold_example` shape decided in the ADR (docs/adr/phase-2-eval-spine.md) so they
load straight into the pgvector KB (rvy.2). Common fields: `id, tenant, engine, input, label_payload,
rubric_dimension, rater_id(s), label_version, split (train|holdout|smoke), created_at`. Per-engine
`label_payload` is defined in §4. The `split` field carries `smoke` for rvy.10 data so smoke labels are never
mistaken for the real holdout.

---

## 3. Hard-negative / hard-case floor (PINNED — cite this figure)

**Every gold set must be ≥30% hard negatives / hard-band cases, with an absolute floor of ≥10 such examples**
(whichever is larger). This is the single citeable figure referenced by rvy.4, rvy.5, and rvy.6. A set that
is mostly easy positives passes the gates while the engine is actually weak; this floor forces the hard band.

"Hard" per engine:
- **Posting**: off-voice **near-misses** — right topic / wrong voice, subtle AI-tells, almost-on-brand tone — NOT trivial gibberish.
- **Outreach**: missing-field, ambiguous-role, and wrong-company traps for the extraction cells; generic-vs-specific hooks for personalization.
- **Engagement**: **sarcasm, veiled complaints, trolling, ambiguous intent**, plus DM/out-of-24h-window cases that must label "route to human."

Class balance: no single class may exceed 60% of a set; the hard band above is counted within these.

---

## 4. Per-engine rubrics (concrete pass/fail)

### 4.1 Posting — on-voice vs off-voice (binary)
Dimensions: tone, vocabulary, sentence structure, claims discipline.
- **On-voice (pass)**: matches the client's established register; no banned phrases / AI-tells; claims are supported.
  *Exemplar*: a caption that reads like the artist wrote it — specific, in their cadence.
- **Off-voice (fail)**: generic marketing voice, AI-tell phrasing ("In today's fast-paced world…"), wrong tone, or unsupported claims.
  *Exemplar (hard)*: on-topic and grammatical but subtly corporate — the near-miss the gate must catch.

### 4.2 Outreach — personalization quality + extraction ground truth
- **Extraction** (objective ground truth, normalized — case/whitespace/titles per ADR): `prospect_name, role, company, hook`. Pass = exact normalized match.
- **Personalization quality** (judged, per-rater labels): 0=mail-merge generic · 1=shallow (name only) · 2=specific & relevant hook tied to the prospect.
  *Hard exemplars*: missing role, two plausible companies, a hook that sounds specific but is generic.

### 4.3 Engagement — triage class + reply-safety
- **Triage class** (taxonomy): `positive · question · complaint · spam · lead`.
- **Reply-safety** (binary): `safe-to-auto` vs `must-escalate`. **All DMs label `must-escalate` (route to human)** regardless of class; comments may be `safe-to-auto` within the taxonomy.
  *Hard exemplars*: sarcasm read as positive; a veiled complaint; a troll that is "safe to ignore" vs one that "must escalate"; a DM that looks routine but still routes to human.

---

## 5. Multi-rater process & agreement

- Subjective labels (posting on/off-voice; outreach personalization; engagement triage/safety) require **≥2 raters labeling blind** (independently, not seeing each other's labels). Objective extraction fields need a single ground-truth pass.
- Record **per-rater labels** (not a pre-collapsed value) so agreement is computable.
- Acceptance: **Cohen's κ ≥ 0.6** per subjective dimension.
- **κ < 0.6 → adjudication loop** (mandatory, not "try again"): (1) a third rater / owner adjudicates the disputed items; (2) the rubric is refined where the disagreement clustered; (3) the disputed items are **re-labeled** under the refined rubric; (4) κ recomputed. Repeat until κ ≥ 0.6. Log each round.
- Ambiguous items with no defensible label go to an explicit **`uncertain/exclude`** bucket — excluded from P/R so they don't pollute the metric.

---

## 6. Protocol acceptance — the PILOT GATE (replaces "label from this doc alone")

This protocol is accepted only when it is **proven teachable**, not asserted readable:

> **Pilot gate:** 2 raters independently label **N = 12 pilot examples** per engine using **only this document**
> (no verbal coaching), and must reach **Cohen's κ ≥ 0.6**. If κ < 0.6, run the §5 adjudication loop, refine
> this doc where raters diverged, and re-pilot on a fresh 12 until the bar is met. The passing pilot (per-rater
> labels + computed κ) is recorded as the protocol's evidence of fitness.

A protocol that can't get two raters to κ≥0.6 on 12 doc-only examples is not done — it will not produce a
trustworthy gold set at scale.

---

## 7. Sizing & versioning

- **Per-engine size**: 30–200 labeled examples. **Posting is ≥100** so the spec §5 blind **100-post brand-voice holdout** is satisfiable (rvy.4); the holdout is disjoint from any brand-voice grounding data (KNOW-02).
- The §3 hard-negative floor (≥30%, min 10) applies to every set.
- **Label versioning**: any rubric change bumps `label_version`; old metrics stay attached to their version so a re-label never silently invalidates history; new evals compare like-for-like.

---
*Owner: pm. Created 2026-06-28 for CustomerAcq-rvy.3. Hard-negative floor (§3) is the citeable figure for rvy.4/rvy.5/rvy.6.*
