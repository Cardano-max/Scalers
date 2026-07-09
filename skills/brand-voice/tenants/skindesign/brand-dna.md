# Brand DNA — skindesign (Skin Design Tattoo)

> Tenant: `skindesign` · Display: "Skin Design Tattoo" · Artist: multi-artist studio
> (per-location roster; see `client-data/artists.csv`)
> Resolved by pack ref `brand-voice/skindesign`. Source of truth for all
> `skindesign` tenant-facing copy. Keep in sync with `examples.jsonl`.
>
> **REAL CLIENT pack (the one paying client).** Authored **2026-07-03** by writer
> from Skin Design Tattoo's own materials **only** — `client-data/artists.csv`,
> `client-data/customers.csv`, and `client-data/campaign-examples.json` (5 real SMS
> campaigns manually transcribed from operator-provided screenshots; provenance in
> that file's `_provenance` block). Nothing here is inferred, embellished, or
> imported from the `ladies8391` smoke tenant.
>
> **Fill rule (HARD anti-fabrication):** every field must trace to real client
> evidence. No evidence yet → a literal `TODO(operator-verify): <question>` line,
> **never** an invented claim/offer/stat/positioning. `TODO` in **Positioning** →
> skill graceful-degrades on that field; `TODO` in **Approved claims** → that claim
> is unusable (draft blocks + escalates) until the operator fills it. This pack
> becomes brand-"real" only when the operator signs off voice/tone AND the human
> rater signs off the holdout (see `docs/onboarding/client-onboarding-playbook.md`
> Step B). Until then all external copy is DRAFT.
>
> **Every "Approved claim" below carries an inline `[source: …]` citation** to the
> exact client-data file/field it traces to. A claim with no such citation must not
> exist in this pack.

---

## Positioning

- **One-line promise:** TODO(operator-verify): Skin Design Tattoo has no
  client-stated tagline / one-line promise in `client-data/`. Do NOT invent one.
  What is *observable* from the campaigns: a multi-location studio that runs
  artist-led, limited-spot full-day-session promos to its own past/returning
  clients, with flexible payment plans. Confirm the canonical positioning line.
- **Who they are:** A **multi-location tattoo studio** operating under the "Skin
  Design Tattoo" name across six locations — **Spring Mountain, Orange County,
  Soho, Hawaii, New York, and Nashville**
  `[source: client-data/artists.csv studio_name column; client-data/campaign-examples.json _provenance.client]`.
  Each location carries its own artist roster (`client-data/artists.csv`); several
  artists appear across multiple locations (see that file for the per-location
  roster — not reproduced here to keep individuals' contact PII off this
  prompt-prepended surface).
  - TODO(operator-verify): studio ownership / founder identity and how copy may
    reference them. One roster name recurs across most locations (see
    `client-data/artists.csv`), which is *consistent with* a founder/principal, but
    this is **not stated** in client-data — do not assert it, and confirm with the
    operator before naming any individual as owner in copy.
  - TODO(operator-verify): city/state for each location name (e.g. is "Spring
    Mountain" the Las Vegas flagship? the campaign sender is a +1 **702** number,
    which is the Las Vegas area code — suggestive, not confirmed). Do not print a
    city until confirmed.
  - TODO(operator-verify): years in business / year established. No evidence in
    client-data.
  - TODO(operator-verify): house style(s) / what defines the work (black-and-grey,
    realism, color, etc.). The campaigns describe **session types** (full-day,
    larger pieces, cover-ups, finishing work) but never a style. Do not assert a
    style.
- **What they are NOT:** TODO(operator-verify): no client-stated anti-positioning.
  Do NOT invent styles/tones they "never adopt." (Platform-default guardrails that
  always apply regardless — no unverifiable claims, no medical/healing promises, no
  superlatives — live under *Do-not* below; those are ours, not the client's.)
- **Proof points (verifiable → feed Approved claims):**
  - Multi-location (six named locations) `[source: artists.csv; campaign-examples.json _provenance]`
  - Offers **full-day tattoo sessions** `[source: campaign-examples.json — Angel, Bella, Keebs campaigns]`
  - Full-day sessions suit **larger pieces, finishing/adding to existing work, and
    new custom pieces** `[source: campaign-examples.json — Angel campaign message_copy, F0BEPFG99HQ.jpg]`
  - **Payment plans via Klarna & Affirm** `[source: campaign-examples.json — Angel campaign message_copy]`

## Personas (1–3)

Grounded only in who the real campaigns actually targeted. Deeper psychographics
are **not** in client-data → TODO, not invented.

### 1. The returning / past client
- **Label:** returning client re-engaged by an artist promo.
- **In their words:** TODO(operator-verify): no verbatim client-voice quotes exist
  in client-data (`customers.csv` is name/email/phone only). Do not fabricate quotes.
- **Fears / frictions:** TODO(operator-verify): not evidenced.
- **Desire / JTBD:** book time with **a specific artist** during a **limited
  booking window**, for a larger/custom piece or to finish existing work — with the
  option to **split payment**. `[source: campaign-examples.json — Angel campaign:
  "short booking window for returning clients", offer_type "returning clients";
  payment_plans "Klarna & Affirm"]`

### 2. (additional personas)
- TODO(operator-verify): any audience beyond "returning/past clients" (e.g.
  first-timers, walk-ins, referrals). The 5 campaigns all target existing lists;
  client-data shows no other segment. Do not invent one.

## Messaging pillars (3–5)

Each pillar is a **pattern actually observed** across the 5 real campaigns
(`campaign-examples.json.observed_patterns`). Every piece of copy ladders to one.

1. **Artist-led promo** — a named artist opens availability; the offer is framed
   around *that artist* (keyword CTA = the artist's name). `[source: Angel/Bella/Keebs/Lynn campaigns; observed_patterns "artist-specific full-day special", "reply-{ARTIST-NAME} keyword CTA"]`
2. **Limited-spots scarcity / short window** — a small number of spots or a short
   booking window, often with a scarcity follow-up ("DOWN to 2 SPOTS LEFT") 1–6
   days later. `[source: Angel opener "limited 5 spots" → follow-up "2 SPOTS LEFT"; observed_patterns "limited-spots scarcity", "initial blast + scarcity follow-up sequence"]`
3. **Flexible payment** — full-day work is made approachable via **Klarna &
   Affirm** ("if you'd rather split it up"). `[source: Angel campaign; observed_patterns "payment plan angle"]`
4. **Personal reconnect** — warm, personal outreach to past clients ("X wanted me
   to personally reach out"), friendly and low-pressure. `[source: Lynn campaign; observed_patterns "personal-outreach framing"]`

## Sensitive subjects (feeds the APPROPRIATENESS jury dimension)

- **Sensitive:** **cover-up / finishing existing work** touches personal and
  body-image territory `[source: Angel campaign — "finishing work, adding on"]`;
  handle a client's existing tattoo or the reason for a cover-up with dignity, in
  the client's framing — never as a "before/after", "fix", or "glow-up" sell.
  - TODO(operator-verify): whether Skin Design does scar / post-surgery / memorial
    work, and how they want it handled. Not evidenced in client-data — do not
    promote it, and do not adopt `ladies8391`'s reclaiming/post-mastectomy stance
    (that is a different, synthetic tenant).
- **Out-of-scope / off-positioning:** TODO(operator-verify): the client has not
  stated services they do NOT offer. Do not assert any. (Platform-default: copy
  never gives **medical/clinical advice** — healing problems, allergic reactions,
  numbing/pain — and escalates such questions to a human.)

## Approved claims (allow-list) — the compliance surface

The **ONLY** factual/credential/offer claims `skindesign` copy may make. A claim not
on this list → **block + escalate**. Each entry carries its client-data source.

- "Skin Design Tattoo is a multi-location tattoo studio, with locations at Spring
  Mountain, Orange County, Soho, Hawaii, New York, and Nashville."
  `[source: client-data/artists.csv studio_name; client-data/campaign-examples.json _provenance.client]`
- "Offers full-day tattoo sessions."
  `[source: client-data/campaign-examples.json — Angel (F0BEPFG99HQ.jpg), Bella (F0BFHR5ULJC.png), Keebs (F0BEPFJTMQS.png) campaigns]`
- "Full-day sessions are great for larger pieces, finishing or adding to existing
  work, or starting a new custom piece."
  `[source: client-data/campaign-examples.json — Angel campaign message_copy, F0BEPFG99HQ.jpg]`
- "Payment plans are available through Klarna and Affirm."
  `[source: client-data/campaign-examples.json — Angel campaign message_copy / payment_plans field]`
- "Check availability, get a quote, or ask about payment-plan options by replying
  with / texting the artist's name."
  `[source: client-data/campaign-examples.json — Angel cta ("Reply ANGEL…"), Keebs cta ("Text KEEBS for available dates or payment plan options")]`

> **Prices are NOT standing claims.** The campaigns show real per-campaign prices
> ($1,200 full-day for Angel/Keebs; $500 full-day for Bella) but these are
> **campaign-specific specials**, not a published price list. A specific price may
> appear in copy **only** when it traces to that campaign's operator/artist-approved
> offer — never asserted by this pack as a standing rate. Any price with no
> campaign source → block + escalate.
>
> **NOT approved (block + escalate):** superlatives ("best", "#1", "world-class");
> pain/painless promises; healing/outcome guarantees; medical advice; any price or
> discount not tied to an approved campaign offer; mentions of other studios/artists
> by comparison; awards or credentials (licensing, years in business, style
> specialties) — **none are in client-data**; and any location city/state until the
> `TODO(operator-verify)` above is filled.

## Voice & tone rules  → emitted as VoiceDimensions.tone / structure / vocabulary.emoji_policy/hashtag_policy

Derived from the **actual copy** in the 5 real SMS campaigns — this is Skin Design's
observed voice, not an aspiration.

- **Register:** warm, friendly, upbeat, low-pressure. Opens with a greeting ("Hi!",
  "Hey!") and often a smile (":)"). Direct and concise — SMS-native. `[source: Bella, Lynn, Keebs campaigns]`  → tone
- **Person / POV:** the studio speaks **on behalf of a named artist** — "Angel
  opened a short booking window", "Lynn wanted me to personally reach out" — a warm
  intermediary voice, not a faceless corporate "we" and not the artist's own first
  person. `[source: Angel, Lynn campaigns]`  → tone
- **Sentence rhythm:** short lines, one idea each; a light scarcity beat; caps used
  sparingly for emphasis on the offer ("FULL-DAY SPECIAL", "2 SPOTS LEFT"). `[source: Angel opener + follow-up]`  → structure
- **Emoji policy:** 0–2, sparing. Observed in real copy: 🔥 (opener) and ":)"
  smiley. Not a hype-wall. → vocabulary.emoji_policy
- **Hashtag policy:** TODO(operator-verify): no evidence — the client's real channel
  is SMS, which carries no hashtags. Default to **none** on any social channel until
  the operator provides an approved tag set; never invent tags. → vocabulary.hashtag_policy
- **CTA style:** a concrete keyword reply/text tied to the artist — "Reply ANGEL to
  check availability or get a quote", "Text KEEBS for available dates or payment plan
  options" — plus the option to ask about payment plans. `[source: Angel, Keebs campaigns]`  → tone

## Do / Do-not  → emitted as VoiceDimensions.vocabulary.prefer / .ban

**Do (preferred lexicon & moves — from real copy):**  → vocabulary.prefer + structure
- Words/phrases the client actually uses: "full day / full-day special / full-day
  session", "booking window", "spots" / "spots left", "returning clients", "check
  availability", "get a quote", "payment plan options", "split it up", "Klarna &
  Affirm", warm openers ("Hi!", "Hey!"), the artist's name as the keyword CTA.
- Moves that work: **lead with the artist** and their opening; state the offer
  plainly; add a light limited-spots beat; offer the **payment-plan** option; end
  with a **keyword CTA**; on SMS, **always** include the STOP opt-out.

**Do-not (absolute bans — beats everything):**  → vocabulary.ban
- TODO(operator-verify): the client's **own** explicit banned-word / don'ts list is
  not in client-data. The bans below are **platform-default guardrails** (ours), not
  client-stated — replace/extend once the operator provides the real don'ts.
- No claim outside the Approved-claims allow-list — especially **no price/discount**
  not tied to an approved campaign offer, **no medical/healing** claims, **no
  pain/painless** promises.
- No superlatives ("best", "#1", "world-class") or awards/credentials — unverified.
- No location city/state, ownership, years, or style claim until its
  `TODO(operator-verify)` is filled.
- **Compliance:** on SMS, never drop the **STOP** opt-out; never message a number on
  the suppression / DND list (delivery data shows DND is a first-class concern —
  `campaign-examples.json.observed_patterns.delivery_reality`).
- AI tells: em-dash-as-drama, rule-of-three padding, contrast framing ("it's not
  just a tattoo, it's…"), generic corporate transitions.
- Do **not** borrow `ladies8391` positioning (women-first, neo-traditional,
  reclaiming/post-mastectomy) — that is a different, synthetic tenant.

## On-voice examples (rhythm anchors — mirror, never copy)

The **verbatim** real campaign copy (`campaign-examples.json`). Machine-readable in
`examples.jsonl` (`label:on_voice`, `split:grounding`). These are the client's own
sent messages — keep disjoint from any future holdout.

1. > 🔥 ANGEL FULL-DAY SPECIAL
   > Angel opened a short booking window for returning clients! For a limited 5
   > spots, we're offering full-day sessions at $1,200 per session. Great for larger
   > pieces, finishing work, adding on, or starting a new custom piece with Angel.
   > Klarna & Affirm payment plans are available if you'd rather split it up.
   > Reply ANGEL to check availability or get a quote. Reply STOP to opt out
   - **Why it works:** Pillars 1+2+3. Artist-led, limited-spots, payment-plan
     option, keyword CTA, STOP opt-out. `[source: F0BEPFG99HQ.jpg]`
2. > We are DOWN to 2 SPOTS LEFT with Angel's $1200 FULL DAY SPECIAL. Text ANGEL now
   > to claim your spot before they are gone. Reply STOP to opt out
   - **Why it works:** Pillar 2 scarcity follow-up to the same audience; caps beat;
     keyword CTA; STOP. `[source: F0BEMF63R0W.png]`
3. > Hi! Are you interested in grabbing a spot for Bella's $500 full day special? :)
   > Please reply STOP to opt out
   - **Why it works:** Warm, short, friendly ask; smile emoji; STOP. `[source: F0BFHR5ULJC.png]`
4. > Hey! Lynn wanted me to personally reach out to you, to see if you were
   > interested in her promo? Reply 'stop' to opt out
   - **Why it works:** Pillar 4 personal reconnect; warm intermediary voice; STOP.
     `[source: F0BE84M3VAT.png]`
5. > Hi! Are you interested in one of Keebs's $1200 full day sessions while spots are
   > still available? Text KEEBS for available dates or payment plan options :)
   > Reply STOP to opt out
   - **Why it works:** Pillars 1+2+3; availability + payment-plan angle; keyword
     CTA; smile; STOP. `[source: F0BEPFJTMQS.png]`

## Off-voice negatives (recommended)

Authored anti-examples (not claims about the client) to sharpen the eval holdout /
AI-flagger. In `examples.jsonl` as `label:off_voice`, `split:negative`.

1. **Hard near-miss (right topic, wrong voice + a fabricated claim):**
   > Book the BEST full-day tattoo in town — 100% pain-free, guaranteed to heal
   > perfect. Only $99 today!!!
   - **Why it's off:** same topic (full-day session) but superlative ("BEST"), pain
     promise, healing guarantee, and an **unapproved price** — every Approved-claims
     ban. Also drops the STOP opt-out.
2. **Corporate / SaaS sludge:**
   > At Skin Design Tattoo, we leverage world-class artistry to deliver premium,
   > bespoke body-art solutions tailored to your unique journey. Contact our team to
   > learn more.
   - **Why it's off:** faceless corporate "we", no named artist, no real offer, no
     opt-out — the generic voice this skill exists to prevent.
3. **Missing compliance:**
   > Angel has full-day spots open — text ANGEL to grab one!
   - **Why it's off:** on-brand shape but **no STOP opt-out** on an SMS-style
     message — a compliance fail the gate must catch.

---

> **Vetting / registry routing.** This is a per-tenant **content** bundle under the
> `brand-voice` skill family (registry row: `brand-voice`, currently `IN-VETTING`,
> `docs/skills/registry.md`), exactly like `ladies8391` and `ink-studio` — tenant
> packs are data under that one skill, **not** separately-registered skills, so no
> new registry row is added here. sec owns the row; writer hands this bundle to sec
> so it is covered by the `brand-voice` vetting and available the moment that row
> graduates to `REGISTERED-IN-USE`. No bundled third-party script is introduced.
>
> *Authored by writer 2026-07-03 from `client-data/` only. Sign-offs pending
> (operator brand + human-rater holdout) — pack is DRAFT until both are recorded.*
