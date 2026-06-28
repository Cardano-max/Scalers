# AI-flagger humanize validator — ruleset spec (1mk.3)

**Author:** writer · **Implements:** eng2 (HARN-02 validator bank) · **Backs:** the
operator's two rules — *"make the AI-flagger a deterministic VALIDATOR, not
optional"* and *"bake the do's/don'ts into the orchestrator."*

This is the precise, testable ruleset behind the no-AI-tells gate. Each rule has a
**deterministic detect**, a **fix disposition** (auto-fix vs flag-for-regen), a
**severity**, and a **test example**. Source of the tells:
`engine/kb/corpus/sources/skills-dos-donts.md` (+ `winning-strategies-kb.md`).

It is the authoritative superset of the live detector
(`engine/cells/ai_flagger.py`): rules **AF-01..AF-04** are implemented today;
**AF-05..AF-08** are specified here for eng2 to wire. Nothing here is optional —
every rule runs as part of the bank on every writing cell.

## Policy (non-negotiable)

- **Deterministic, pure code.** No model call in detection (reproducible; feeds the
  validator-pass-rate metric). Same input → same verdict.
- **HARD gate, every writing cell.** The bank runs on every tenant-facing text
  field (see *Scope*). ERROR issues block the value and trigger repair; the value
  never flows downstream un-repaired.
- **Not optional, not per-cell opt-in.** A writing cell cannot ship copy without
  this bank. (CLAUDE.md skill-use rule + CI `check_skill_registry.py` keep the
  skill itself registered; this spec keeps the *gate* mandatory.)

## Scope — fields the bank runs on

| Cell | Field(s) |
|---|---|
| `content_brief` | `caption`, `headline` |
| `copywriter` | every variant's `hook`, `caption`, `call_to_action` |
| `reply` | `text` |
| `humanize` | `text` (re-checked on its OWN rewrite) |
| outreach (1mk.7, when built) | subject + body |

New writing cells inherit the bank by construction — wire it in `*_validators()`.

## Fix dispositions

- **AUTO-FIX** — a deterministic, **meaning-preserving** transform applied *before*
  the gate re-checks. Only for tells whose removal cannot change intent
  (`normalize_ai_tells`). If an auto-fix fully clears the tell, no ERROR is raised.
- **FLAG-FOR-REGEN** — a **semantic or structural** tell. Detection raises an ERROR
  (or WARN); the fix is a *rewrite*, routed to the `humanize` cell (form b) or a
  cell repair — **never** auto-edited in code (meaning risk).

| Rule | Tell | Disposition | Default severity | Status |
|---|---|---|---|---|
| AF-01 | em-dash / `--` as punctuation | **AUTO-FIX** | ERROR | implemented |
| AF-02 | contrast framing ("not X, it's Y") | FLAG-FOR-REGEN | ERROR | implemented |
| AF-03 | rule-of-three (rhetorical triad) | FLAG-FOR-REGEN | WARN¹ | implemented |
| AF-04 | generic transitions / openers | FLAG-FOR-REGEN | ERROR | implemented |
| AF-05 | banned slop phrases (lexicon) | FLAG-FOR-REGEN | ERROR | partial² |
| AF-06 | hedging / weasel filler | FLAG-FOR-REGEN | WARN³ | **to add** |
| AF-07 | listicle cadence | FLAG-FOR-REGEN | WARN | **to add** |
| AF-08 | emoji-bullet lines | **AUTO-FIX**⁴ | WARN | **to add** |

¹ One triad is allowed (`max_triads=1`); flagged beyond. Per-cell knob may raise to
ERROR for short captions. ² `banned_phrases()` exists; extend the lexicon (AF-05
below). ³ ERROR for captions/hooks where hedging kills the copy; WARN elsewhere —
per-cell knob. ⁴ Auto-strip the *leading decorative bullet emoji* only; inline
emoji are untouched and remain subject to the tenant's brand-voice emoji policy.

---

## Rule catalog

### AF-01 — em-dash / double-hyphen (AUTO-FIX, ERROR)

**Tell:** em/en dash, or `--`, used as dramatic punctuation — the single most
common AI tell.

**Detect** (implemented):
```
—|–|(?<=\s)--(?=\s)|(?<=\w)--(?=\w)
```
Budget `max_em_dashes=0` (any flags). A plain spaced hyphen ` - ` is allowed.

**Fix — AUTO (`normalize_ai_tells`):** replace ` — ` / `--` with `, ` (comma),
collapse double spaces. Meaning-preserving; re-check passes with no ERROR.

**Test:**
| Input | Detect | After auto-fix |
|---|---|---|
| `Fine line is unforgiving — one pass.` | em_dash ×1 | `Fine line is unforgiving, one pass.` (clean) |
| `clean - simple work` | none | unchanged |

### AF-02 — contrast framing (FLAG-FOR-REGEN, ERROR)

**Tell:** "it's not X, it's Y" / "not just X but Y" / "not X but rather Y".

**Detect** (implemented — the four `_CONTRAST_RES` patterns), e.g.:
```
\bit'?s\s+not\s+[^.,;:]{1,40}?[,;]\s*(?:but\s+)?it'?s\s+
\bnot\s+(?:just|only|merely|simply)\s+[^.,;:]{1,50}?\s+but\s+
```
**Fix:** FLAG-FOR-REGEN → humanize rewrite (changing the rhetorical structure is a
meaning decision). Never auto-edit.

**Test:**
| Input | Detect |
|---|---|
| `It's not just a tattoo, it's a statement.` | contrast_framing ✓ |
| `We do custom work, not flash.` | none (no "it's X" pivot) |

### AF-03 — rule-of-three / rhetorical triad (FLAG-FOR-REGEN, WARN→ERROR knob)

**Tell:** three short parallel items — "clean, bold, timeless".

**Detect** (implemented `_TRIAD_RE`): three comma-separated groups of ≤3 words
each; **two commas required** (so ordinary "X, Y and Z" with one comma does not
trip). Budget `max_triads=1`.

**Fix:** FLAG-FOR-REGEN. **Guardrail:** keep the one-triad budget to avoid
flagging legitimate short lists; raise to ERROR only on hooks/headlines via the
per-cell config.

**Test:**
| Input | Detect |
|---|---|
| `clean, bold, timeless ink` | rule_of_three ✓ (beyond budget if another triad present) |
| `small, meaningful pieces` | none (only two items) |

### AF-04 — generic transitions / openers (FLAG-FOR-REGEN, ERROR)

**Tell:** machine connective tissue — "Moreover", "In conclusion", "When it comes
to", "In today's…", "At the end of the day", "Needless to say".

**Detect** (implemented `_TRANSITION_RE`, whole-phrase, case-insensitive).
**Extend the wordlist** with: `to be fair`, `all in all`, `with that said`,
`first and foremost`, `last but not least`, `rest assured`, `truth be told`.

**Fix:** FLAG-FOR-REGEN. (A leading transition *could* be auto-deleted +
re-capitalized, but mid-sentence cases and meaning shifts make a blanket auto-fix
unsafe — leave to the rewrite.)

**Test:**
| Input | Detect |
|---|---|
| `Moreover, we take our time.` | generic_transition ✓ |
| `In today's world, ink matters.` | generic_transition ✓ (`in today's`) |

### AF-05 — banned slop lexicon (FLAG-FOR-REGEN, ERROR)

**Tell:** marketing/AI slop words from the do's/don'ts.

**Detect:** extend `validators.DEFAULT_AI_TELLS` (case-insensitive substring,
word-boundary where possible) with the dos-donts set:
```
unleash, elevate your, level up, dive in, dive into, game-changer, game changer,
look no further, one-stop, transform your, supercharge, take it to the next level,
in the realm of, navigating the, testament to, when it comes to your,
tapestry, delve, delve into, in conclusion, it is important to note,
in today's fast-paced world, as an ai, as a language model
```
**Fix:** FLAG-FOR-REGEN (word-choice change is the artist/voice's call).
**Guardrail:** keep the list curated and tenant-agnostic; per-tenant bans live in
the brand-voice DNA Do-not list (don't duplicate here).

**Test:**
| Input | Detect |
|---|---|
| `Ready to unleash your story?` | banned_phrases: `unleash` ✓ |
| `Elevate your look today.` | banned_phrases: `elevate your` ✓ |

### AF-06 — hedging / weasel filler (FLAG-FOR-REGEN, WARN→ERROR knob) — TO ADD

**Tell:** noncommittal padding that drains conviction — "it's worth noting",
"arguably", "in many ways", "to some extent", "generally speaking", "that said",
"more often than not", "for what it's worth", "it could be argued", "somewhat",
"perhaps", "kind of", "sort of".

**Detect:** a dedicated `_HEDGE_RE` wordlist (whole-phrase, case-insensitive),
separate from transitions:
```
(?<!\w)(it'?s worth noting|worth noting|arguably|in many ways|to some extent|
generally speaking|more often than not|for what it'?s worth|it could be argued|
somewhat|kind of|sort of|perhaps)(?!\w)
```
**Fix:** FLAG-FOR-REGEN. **Severity:** ERROR for `hook`/`headline`/short captions
(hedging kills a hook), WARN elsewhere — per-cell config (`hedge_severity`).
**Guardrail:** "perhaps/somewhat" can be legitimate; require the configurable
severity and allowlist so a single soft word in a long body is WARN, not a block.

**Test:**
| Input | Detect | Severity (hook) |
|---|---|---|
| `Arguably the best placement for this.` | hedging: `arguably` ✓ | ERROR |
| `It's worth noting we book fast.` | hedging ✓ | ERROR |
| `This piece is somewhat large.` (body) | hedging ✓ | WARN |

### AF-07 — listicle cadence (FLAG-FOR-REGEN, WARN) — TO ADD

**Tell:** the AI listicle shape — a "Here are N …" opener and/or ≥3 short parallel
bullet lines with imperative/parallel stems. Reads like a content mill, not a
person in a feed.

**Detect:** two signals, either trips:
1. Opener regex: `\bhere(?:'?s| are)\s+\d+\s+\w+`  (e.g. "Here are 5 reasons").
2. Bullet-line count: ≥3 lines matching
   `^\s*(?:[-*•]|\d+[.)]|[\U0001F300-\U0001FAFF☀-➿])\s+\S`
   in a single field → structural listicle.

**Fix:** FLAG-FOR-REGEN (collapsing a list into prose is a rewrite). **Guardrail:**
a legitimate 2-item list or a single hashtag line must not trip — hence the **≥3**
threshold and the opener requiring a digit.

**Test:**
| Input | Detect |
|---|---|
| `Here are 3 reasons to book:` | listicle (opener) ✓ |
| 3 lines each `✅ …` | listicle (bullet count ≥3) ✓ |
| a 2-line list | none |

### AF-08 — emoji-bullet lines (AUTO-FIX, WARN) — TO ADD

**Tell:** lines led by a decorative emoji bullet (✅🔥👉➡️•) — the LinkedIn/AI
post tell. Distinct from *content* emoji (🖤 in a caption) and from the tenant's
emoji policy.

**Detect:** ≥2 lines matching `^\s*([\U0001F300-\U0001FAFF☀-➿•])\s+\S`
(a leading bullet-emoji followed by text). The **≥2** threshold avoids flagging a
single decorative line.

**Fix — AUTO:** strip the leading bullet emoji + following whitespace from each
such line (cosmetic; the line text is preserved). Re-check then clears.
**Guardrail:** only the *leading* token is stripped; inline emoji mid-line are
untouched and remain governed by the brand-voice per-tenant emoji policy (do not
let this rule fight that one).

**Test:**
| Input | Detect | After auto-fix |
|---|---|---|
| `✅ custom design\n✅ free consult` | emoji_bullet ✓ | `custom design\nfree consult` |
| `Healed and settled 🖤` | none (emoji not leading a line as a bullet) | unchanged |

---

## Wiring guidance (eng2)

- **Where:** add AF-05..AF-08 to `engine/cells/ai_flagger.py` as new `AiTellKind`
  members + detectors, mirroring AF-01..AF-04; expose per-kind severity on
  `FlaggerConfig` (e.g. `hedge_severity`, `listicle_severity`, `emoji_bullet_severity`)
  and budgets where relevant.
- **Auto-fix order:** extend `normalize_ai_tells` with AF-08 leading-emoji-bullet
  stripping (AF-01 already there). Auto-fix runs **before** the ERROR gate so a
  fully-fixed tell does not block.
- **Bank composition:** the `ai_flagger("<field>", config)` validator already plugs
  into any `ValidatorBank`; ensure every writing cell in *Scope* includes it (the
  copywriter/reply cells already do, over their nested fields).
- **English-only detectors:** AF-02/04/05/06 are English wordlists — keep them
  behind the existing `english_only`/`_looks_english` guard to avoid foreign-
  language false positives. AF-01/07/08 are language-agnostic.
- **Allowlist:** the existing `FlaggerConfig.allowlist` exempts specific spans;
  use it for legitimate brand terms that would otherwise trip a lexicon rule.
- **Metrics:** every ERROR feeds the validator-pass-rate; AF-06/07 defaulting to
  WARN keeps them observable without over-blocking until tuned on the gold set.

## Relationship to other gates

- **Brand-voice (S2, `1mk.2`)** owns *per-tenant* Do-not lexicon + emoji policy;
  this spec is the *universal* AI-tell gate. No duplication: tenant bans live in
  the DNA, slop bans live here.
- **humanize cell (`1mk.3` form b)** is the rewrite path for every FLAG-FOR-REGEN
  tell; it re-runs this bank on its own output, so a rewrite that still trips a
  rule is repaired or fails on a code path.
- **Eval gate (rvy.7/.8)** measures the detector on the gold/red-team sets;
  AF-06..AF-08 thresholds should be tuned there (recall vs false-positive) before
  their severities are raised from WARN to ERROR.

## Done-when

- [ ] AF-05 lexicon extended; AF-06/07/08 detectors added with per-kind config.
- [ ] AF-08 leading-emoji-bullet auto-fix in `normalize_ai_tells`.
- [ ] Unit tests per rule using the test examples above (detect + fix).
- [ ] Bank confirmed wired on every writing cell in *Scope*.
- [ ] Thresholds tuned on the gold set (rvy.7/.8) before any WARN→ERROR promotion.
