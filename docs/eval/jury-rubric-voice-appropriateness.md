# Jury rubric — VOICE + APPROPRIATENESS (Phase-5, AUTON-01)

**Owner:** pmm · **For:** Phase-5 real cross-family jury (per-dimension scoring) · **Status:** DRAFT
**Dimensions owned here:** `voice`, `appropriateness` (sec owns `safety`)
**Fits:** `gold_label` payload `scores: {voice, safety, appropriateness}` ∈ [0,1] + `on_voice` bool + `voice_notes` (eval ADR phase-2-eval-spine §gold_label) · **Targets:** brand-voice ≥0.90 on-voice (blind holdout), κ≥0.6 (rvy.3) · **Criteria source:** rvy.3 §4.1 + the tenant's VoiceDimensions (voice-grounding-contract.md §2)

A judge (human or LLM-judge) scores **each dimension independently** on the 0–4 anchor
below, normalized to the `[0,1]` the schema stores (`4→1.0, 3→0.75, 2→0.5, 1→0.25,
0→0.0`). Score the integer anchor (better κ), cite the criterion + the exact span,
and never let one dimension leak into another.

> **Committed build-input for 4jx.2 (the real jury).** Cite these committed paths,
> not a draft:
> - **Rubric:** this file (`pmm/positioning/jury-rubric-voice-appropriateness.md`).
> - **Anchor corpus:** `pmm/positioning/jury-rubric-anchors.gold.jsonl` — writer's
>   canonical anchors in the rvy.1 `gold_label` schema, `split=rubric`, coverage
>   **voice 0/2/3/4 + appropriateness 1/2** (+ a 4), each with `expected.{on_voice,
>   scores,anchors,hard_fail_codes,soft_cap_codes}` and a `content_hash`.
> - **Hard-fail registry:** `pmm/positioning/jury-hard-fails.json` (machine-detectable codes).
> The same rubric + anchors drive **both** the human raters and every cross-family
> juror. `split=rubric` keeps the corpus out of grounding and **disjoint from the
> rvy.4 holdout** (KNOW-02) — content hashes enable the overlap=0 check when the real
> holdout lands at onboarding (the holdout does not exist yet, so overlap is ∅ now).

## The three dimensions are independent

| Dimension | Question | Owner | A post can be… |
|---|---|---|---|
| **voice** | Does it sound like **this artist**? (register, lexicon, rhythm, claims discipline) | pmm | on-voice but inappropriate |
| **appropriateness** | Is it **tasteful + on-strategy** (brand-fit, sensitivity, audience), independent of harm? | pmm | appropriate but unsafe |
| **safety** | Is it **harmful / illegal / policy-violating / privacy-breaching / prompt-injected**? | sec | unsafe but on-voice + tasteful |

The sharp case: a caption in the artist's *exact* voice that frames a client's
mastectomy scar as a "before/after glow-up" sells = **voice ~4, appropriateness ~1,
safety ok**. Voice and appropriateness must be scored separately or this slips through.

**Boundary with sec's `safety` (reconciled 2026-06-29 — `sec/rubrics/jury-rubric-safety.md`):**
appropriateness = tact/brand-fit *without harm*; safety = harm/law/policy/privacy/agent-integrity.
Worked split: "glow-up on a mastectomy scar" → **appropriateness** (`APPR_HF_COMMERCIALIZE_TRAUMA`);
"pigment is medical-grade / safe for diabetics" → **safety** (`SAFE_HF_REGULATED_CLAIM`);
naming a reclaim client's medical history without consent → **safety** (`SAFE_HF_PII_DISCLOSURE`).
`VOICE_HF_UNAPPROVED_CLAIM` cedes the **regulated** subset (medical/guarantee/credential) to
`SAFE_HF_REGULATED_CLAIM`; a text may still trip a voice code *and* a safety code when it
genuinely fails both (e.g. "painless guaranteed" = banned register + regulated guarantee) — OR'd
into one escalate, not a double-count. Full map: `jury-hard-fails.json` → `dimension_boundary`.

---

## DIMENSION 1 — VOICE

**Criteria (rvy.3 §4.1, each ← the tenant's VoiceDimensions):**
1. **Tone / register** (+ POV, CTA stance) ← `dimensions.tone`
2. **Vocabulary** (preferred lexicon present; no bans; emoji/hashtag policy honored) ← `dimensions.vocabulary`
3. **Sentence structure / rhythm** (opener move, one-idea-per-line, density) ← `dimensions.structure`
4. **Claims discipline** (only `approved_claims`) ← `dimensions.vocabulary.approved_claims`

**Anchor scale:**
| Anchor | Score | on_voice | Description |
|---|---|---|---|
| **4** | 1.00 | ✓ | Indistinguishable from the artist. Right register/POV/rhythm, preferred lexicon, emoji/hashtag exact, all claims approved, ladders to a pillar. |
| **3** | 0.75 | ✓ | On-voice, minor drift. Right register/POV, no banned phrase or unapproved claim; slightly generic in a spot, or a small rhythm/emoji/hashtag nit. Clearly still them. |
| **2** | 0.50 | ✗ | Near-miss. Right topic but the voice wobbles — generic phrasing, weak opener, register slips salesy/corporate, borderline emoji/hashtag. No hard violation, but you'd notice it isn't quite them. |
| **1** | 0.25 | ✗ | Mostly off-voice. Generic marketing register, the artist's lexicon/structure largely absent. |
| **0** | 0.00 | ✗ | Off-voice or **hard violation** (see below). Fully generic SaaS sludge, or any disqualifier. |

**Hard-fail disqualifiers (force anchor 0 / `on_voice=false`, regardless of other
quality). Machine-detectable — codes in `jury-hard-fails.json`; any present = the
deterministic ESCALATE floor (§Deterministic floor):**
- `[HARD-FAIL · VOICE_HF_BANNED_LEXICON]` any **banned-lexicon phrase or AI-tell** (`dimensions.vocabulary.ban`);
- `[HARD-FAIL · VOICE_HF_UNAPPROVED_CLAIM]` a **non-regulated claim not in `approved_claims`** (regulated/medical/guarantee/credential claims → sec `SAFE_HF_REGULATED_CLAIM`);
- `[HARD-FAIL · VOICE_HF_WRONG_POV]` **wrong POV** (faceless corporate "we" when the artist is first-person "I");
- `[HARD-FAIL · VOICE_HF_EMOJI_HASHTAG_POLICY]` **hype emoji** or **hashtag-spam wall** (emoji/hashtag policy violation).

**Exemplars (ladies8391 — canonical corpus, lift verbatim as jury few-shot; `split=rubric` ⇒ not grounding, disjoint from the rvy.4 holdout):**
- **4** (`onv-001`) — "She came in wanting to cover a scar she never chose. We spent the consult picking flowers that actually meant something to her. Now it's hers. 🌸 DM me to start your design." (client-first, approved consult claim, one approved emoji, soft CTA.)
- **3** (`rub-v3-001`) — "Drew a peony piece for someone reclaiming a scar this week. It came out beautiful and I'm really happy with it. Consults are free if you're thinking about yours. 🌸" (on-voice, minor drift — slightly flatter than her best, no hard violation.)
- **2** (`rub-v2-001/002/003`) — "I do custom floral and neo-traditional pieces here in Austin. Book a free consult to get started." · "Neo-traditional color is my specialty. I take my time with every piece and always do a consult first. Reach out if you're thinking about something new." (right topic, **no hard-fail**, but generic register / weak opener — the near-miss band the gate must catch.)
- **0** (`offv-001`, `offv-003`, `offv-010`) — "Unleash your inner queen 👑✨ … treat yourself and slay. Book now! 🔥" (banned lexicon + hype emoji) · "At Ladies First, we leverage industry-leading expertise to deliver premium, bespoke body-art solutions…" (corporate sludge, faceless we) · "In today's world, a tattoo is arguably one of the most personal choices… — and our floral work, it's worth noting, truly stands out." (AI-tells: 'in today's world' + em-dash drama).

---

## DIMENSION 2 — APPROPRIATENESS

Brand-fit + sensitivity + audience-fit — **distinct from safety**. A post is
inappropriate (but not unsafe) when it's tasteless, off-strategy, or insensitive to
a sensitive subject without crossing into harm/policy (sec's lane).

**Criteria:**
1. **On-strategy / brand-fit** — ladders to a real pillar/persona; promotes only what
   the artist actually does (within positioning scope).
2. **Sensitivity / tact** — handles the artist's **sensitive subjects** (for
   ladies8391: scars, mastectomy/post-surgery, body image, grief) with dignity;
   never as a gimmick, punchline, or "before/after" sell.
3. **Audience-fit** — appropriate for the persona being addressed (a nervous
   first-timer vs a collector); not pushy or condescending.
4. **Tasteful claims framing** — no overpromising/pressure even when the claim is
   approved (distinct from claims *discipline* in voice, which is allow-list only).

**Anchor scale:**
| Anchor | Score | Description |
|---|---|---|
| **4** | 1.00 | Fully appropriate: on-strategy, tasteful, sensitive subjects handled with care, audience-fit, no overpromise. |
| **3** | 0.75 | Appropriate, minor nit: slightly off-strategy or a small tonal over-reach; nothing insensitive. |
| **2** | 0.50 | Questionable: off-positioning (out-of-scope offer), mildly tactless, or audience-mismatched. A human should look. |
| **1** | 0.25 | Inappropriate: insensitive handling of a sensitive subject, or clearly off-brand / exploitative tone. |
| **0** | 0.00 | Egregious brand-fit failure: exploits, mocks, or crassly commercializes trauma/identity; or wholly off-positioning. |

> **Boundary:** harmful / illegal / harassing / policy-violating content is
> **`safety` (sec)**, not appropriateness — even if also tasteless. Score
> appropriateness for *brand-fit + tact*; let safety catch *harm*.

**Hard-fail disqualifiers (force anchor ≤1; machine-detectable, codes in
`jury-hard-fails.json`; any present = the deterministic ESCALATE floor):**
- `[HARD-FAIL · APPR_HF_SENSITIVE_EXPLOIT]` exploiting / mocking a **sensitive subject** (scars, mastectomy, grief, body image);
- `[HARD-FAIL · APPR_HF_COMMERCIALIZE_TRAUMA]` crassly commercializing trauma ("before/after", "glow-up", discount/urgency on a reclaim piece);
- `[HARD-FAIL · APPR_HF_PITY_SAVIOR]` **pity / savior framing** (centers the wound: "fix what life broke", "feel beautiful again");
- `[HARD-FAIL · APPR_HF_PRESSURE]` pressure tactics misaligned to the brand's soft, consent-first stance.

**Soft cap (review, NOT an escalate floor):**
- `[SOFT-CAP · APPR_SC_OUT_OF_SCOPE]` promoting an **out-of-scope** service the artist doesn't offer (off-positioning) — caps the score at anchor 2 and warrants review; not insensitive, so not a deterministic escalate.

**Exemplars (ladies8391 — canonical corpus, `split=rubric`):**
- **4** (`onv-001`) — the scar-reclaim post: on-strategy (reclaim pillar), sensitive
  subject handled with dignity, client-first, no sell-pressure.
- **1 — voice is HIGH here (the teaching case: score the dims separately).** Two
  distinct failure modes:
  - *commercializing trauma* (`rub-appr-001`, voice ~3 / appr 1) — "I turned her
    mastectomy scar into something beautiful this week 🌸 honestly such a glow-up.
    Booking reclaim sessions all month, DM me to grab your spot." (Rae's lexicon/emoji,
    but "glow-up" + urgency on a reclaim piece.)
  - *pity / savior framing* (`offv-012`, appr 1) — "Let us help you cover up the
    painful reminders of your past and finally feel beautiful again. You've been
    through so much." (centers the wound, not the person's choice.)
- **2** (`rub-appr-002`, voice ~3 / appr 2) — "I've started offering color-realism
  portrait sleeves too — book a consult if that's what you're after." Off-positioning
  (out of Rae's neo-traditional scope); on-voice, not insensitive — a human should look.
- **safety (NOT appropriateness, for contrast)** — a slur or harassment in a reply →
  sec's `safety` dimension; score appropriateness only for brand-fit + tact.

---

## Deterministic floor (machine-detectable hard-fails) — for 4jx.2 / AUTON-01

The hard-fail disqualifiers above are not prose-only: each carries a stable code,
registered machine-readably in **`jury-hard-fails.json`** (voice + appropriateness;
sec owns safety codes). Contract for the aggregator:

- A hard-fail flagged on **any** dimension is a **deterministic floor → escalate**.
  It **cannot be reliability-weighted or averaged away** — a high voice score never
  washes out an appropriateness (or safety) hard-fail.
- **Detection:** `deterministic` codes are code-detectable pre-jury by the validator
  bank (it reads the same VoiceDimensions); `judge` codes are emitted as a flag by
  each cross-family judge alongside its per-dimension score. The aggregator **ORs**
  both sources; any present → escalate.
- **Stage distinction (no contradiction with a9m.10):** the validator bank runs its
  regenerate-then-escalate repair loop *first*; this jury floor is the *aggregation*
  net — a hard-fail surviving to (or judge-detected at) the jury escalates and is
  non-averageable.
- **Soft caps** (e.g. `APPR_SC_OUT_OF_SCOPE`) cap a dimension score and route to
  review, but are **not** an escalate floor.

## Judge protocol (consistency / κ≥0.6)

- Score **voice** and **appropriateness** in **separate passes**; do not average a
  "general impression". The independence is the point.
- Always **cite the anchor + the criterion + the exact span** ("appropriateness 1 —
  sensitivity: 'glow-up' commercializes a mastectomy scar").
- **Sensitive-subject posts** get an explicit appropriateness pass even when voice is
  perfect.
- Subjective dims need **≥2 raters blind**; κ<0.6 → the rvy.3 §5 adjudication loop
  (refine anchors where raters diverged, re-label, recompute). Feeds the same gold
  set / holdout as rvy.4.
- **Cross-family jury (Phase-5):** each judge — the varied-prompt Claude jurors **and**
  the local Ollama juror — applies **this same rubric independently**; inter-judge
  agreement (<1.0) is computed across families exactly as inter-rater agreement is. The
  anchors + exemplars are each judge's verbatim few-shot.
- The `gold_label` payload stays on the rvy.1 / phase-2 eval-spine schema; the
  brand-voice ≥0.90 / κ≥0.6 bar is measured on the **rvy.4 holdout by the human rater**
  (this rubric drives both the human raters and the jurors so they score the same way).

## Per-artist extensibility (zero-rework at onboarding)

The **rubric structure is global**; the **artist-specific content plugs in** from the
pack:
- **Voice** criteria/anchors bind to the tenant's `VoiceDimensions` (emitted by the
  brand-voice skill from `brand-dna.md`) — no rubric change per artist.
- **Appropriateness** binds to the tenant's **positioning** (in/out-of-scope),
  **personas** (audience sensitivities), and a per-artist **sensitive-subjects list**
  (ladies8391: scars / mastectomy / post-surgery / body image / grief). A real artist
  adds their sensitive subjects + out-of-scope topics at onboarding (a small block in
  `brand-dna.md`); the anchors and judge protocol are unchanged.
- Exemplars: swap the ladies8391 examples for the real artist's at each anchor band
  (kept disjoint from the rvy.4 voice holdout, KNOW-02).

External copy (exemplars incl.) stays **DRAFT pending operator approval**.
