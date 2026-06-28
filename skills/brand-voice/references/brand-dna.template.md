# Brand DNA — per-tenant template

Copy this file to `skills/brand-voice/tenants/<tenant>/brand-dna.md` and fill every
section with the **artist's real data**. The `brand-voice` skill treats this file as
the source of truth: a cell may only state claims that appear here.

> Filling rule: every field below must be concrete and verifiable. If you do not
> have a value, leave it as `TODO(owner)` — never invent one. A `TODO` in
> Positioning forces graceful-degrade behavior; a `TODO` in Approved claims means
> that claim is **not** usable until filled.

---

## Positioning

- **One-line promise:** <what this artist uniquely delivers, in one sentence>
- **Who they are:** <style/specialty, studio, city, years, what defines the work>
- **What they are NOT:** <explicit anti-positioning — styles/tones to never adopt>
- **Proof points:** <only verifiable facts; these feed Approved claims>

## Personas (audience)

For each primary audience (1–3):

- **Name / label:** <e.g. "First-tattoo planner">
- **In their words:** <how they actually talk about wanting this — real phrasing>
- **Fears / frictions:** <pain, hesitation, what stops them booking>
- **Desire / job-to-be-done:** <what a great outcome looks like to them>

## Messaging pillars (3–5)

Every piece of copy must ladder back to exactly one pillar.

1. **<Pillar name>** — <what it means; the angle it licenses>
2. **<Pillar name>** — <...>
3. **<Pillar name>** — <...>

## Approved claims (allow-list)

The ONLY factual / credential / offer claims the copy may make. A claim not on this
list → block + escalate (do not write it).

- <claim — e.g. "10+ years specializing in fine-line blackwork">
- <claim — e.g. "Custom designs only; no flash copies">
- <offer — e.g. "Free 20-minute consult before any booking">

## Voice & tone rules

- **Register:** <e.g. warm, plain-spoken, confident; not salesy, not clinical>
- **Person / POV:** <e.g. first person "I"; or studio "we">
- **Sentence rhythm:** <e.g. short, punchy; one idea per line>
- **Emoji policy:** <e.g. 0–1, only ⚡/🖤; never 🔥💯>
- **Hashtag policy:** <count + style; banned tags>
- **CTA style:** <how they ask for the next step>

## Do / Do-not

**Do (preferred lexicon & moves):**
- <word/phrase the artist actually uses>
- <structural move that works — e.g. lead with the client's story>

**Do-not (absolute bans — beats everything):**
- <banned word/phrase — e.g. "unleash", "elevate your ink", "dive in">
- AI tells: em-dash-as-drama, rule-of-three padding, contrast framing
  ("it's not X, it's Y"), generic transitions ("In today's world…")
- <off-brand claims, competitor mentions, discounting language if banned>

## On-voice examples (few-shot anchors)

3–10 real, high-performing captions/replies. These are rhythm anchors — mirror,
never copy. The machine-readable copy lives in `examples.jsonl` (loaded to the
`examples_uri` / KB for similarity checks); keep this list in sync.

1. > <verbatim high-performing caption>
   - **Why it works:** <pillar + the move it makes>
2. > <...>

## Off-voice negatives (optional but recommended)

A few examples of copy that is *off* this voice (generic AI, wrong tone, banned
phrasing). Used to sharpen the eval holdout and the AI-flagger. Keep in
`examples.jsonl` with `"label": "off_voice"`.
