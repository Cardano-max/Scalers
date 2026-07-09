# Brand DNA — demo_studio (Copper Fox Tattoo)

> Tenant: `demo_studio` · Display: "Copper Fox Tattoo" · Type: multi-artist studio
> Resolved by pack ref `brand-voice/demo_studio`. Source of truth for all
> `demo_studio` tenant-facing copy. Keep in sync with `examples.jsonl`.
>
> ⚠️ **SYNTHETIC / DEMO TENANT — 100% FICTIONAL. NOT A REAL STUDIO, NOT REAL PEOPLE.**
> Copper Fox Tattoo, its artists, its customers (`demo_studio` personas), addresses,
> emails, and phone numbers are **invented, company-owned demo assets** authored for
> the tlv.6 end-to-end demo slice. Every email uses the RFC-2606 reserved
> `@example.com` domain and every phone uses the reserved `555-0100–555-0199`
> fiction range, so nothing here can resolve to a real person or business. This pack
> is deliberately a **different** studio from `ladies8391` (Ladies First), `ink-studio`,
> and the real client `skindesign` (Skin Design Tattoo) — **zero identity bleed**: do
> not import their artists, cities, positioning, offers, or voice. Because this tenant
> is fictional-by-design, values are invented freely but kept **internally consistent**;
> there are no `TODO(operator-verify)` gaps. **No real-client copy may ever be produced
> against this tenant.**

---

## Positioning

- **One-line promise:** A friendly, craft-first neighborhood studio where every piece
  is drawn for you — five resident artists, five specialties, one calm no-pressure room.
- **Who they are:** Copper Fox Tattoo, an appointment-based custom studio in Denver, CO.
  Five resident artists covering color realism, fine-line botanical, illustrative
  blackwork, bold American traditional, and geometric/cover-up work. Custom-only,
  consultation-first, relaxed and welcoming — a studio built for both first-timers and
  seasoned collectors who want the craft taken seriously without the attitude.
- **What they are NOT:** Not a high-pressure, scarcity-driven promo shop (no "2 spots
  left, book today"). Not a single-style boutique. Not a flash-only walk-in mill (flash
  is a monthly extra, not the core). Not edgy-for-shock, not clinical, not corporate.
- **Proof points (all fictional-but-consistent → feed Approved claims):** five resident
  artists across five named specialties; free 30-minute consultation before every first
  booking; custom work drawn for each client; monthly flash days for walk-up small
  pieces; free touch-ups within 6 months on healed work; payment plans on pieces over
  $500; appointment-based studio in Denver, CO.

## Personas (audience segments — how demo copy should speak)

### 1. The lapsed collector (win-back)
- **In their words:** "I've been meaning to come back and finish my sleeve, life just
  got busy." "I loved my last piece, I just never rebooked."
- **Fears / frictions:** feeling forgotten or like just another booking; not sure the
  artist remembers the plan; guilt about the gap.
- **Desire / JTBD:** a warm, specific nudge that remembers what they were working on and
  makes rebooking easy — not a generic blast.

### 2. The considered first-timer
- **In their words:** "I've wanted one for years but I want it done right." "I don't
  know how to describe what I want."
- **Fears / frictions:** picking the wrong artist/style; feeling rushed or judged;
  permanence.
- **Desire / JTBD:** a calm consult, a clear plan, and an artist whose style fits — no
  pressure to book on the spot.

### 3. The style-matcher
- **In their words:** "I want fine-line botanical, who does that here?" "I'm looking for
  a real blackwork piece, not a copy."
- **Fears / frictions:** getting routed to the wrong artist; watered-down work.
- **Desire / JTBD:** matched to the resident artist whose specialty fits the idea.

## Messaging pillars (every piece of copy ladders to exactly one)

1. **Drawn for you** — custom-only craft; each piece designed for the client, matched to
   the right resident artist and specialty.
2. **Calm and no-pressure** — consultation-first, relaxed room, book when you're ready;
   the anti-scarcity studio.
3. **Remembered, not blasted** — outreach that references the client's own history and
   the piece they were building (the win-back angle), never a generic offer.
4. **Craft you can trust** — free consults, free 6-month touch-ups, honest talk about
   healing and placement; the work carries the post.

## Sensitive subjects (feeds the APPROPRIATENESS jury dimension)

- **Sensitive:** cover-ups and finishing older work touch personal/body-image territory
  — handle with dignity, in the client's framing, never as a "before/after" or "fix"
  sell. Memorial pieces (a fictional service here) are handled with care, never as a
  hook or upsell.
- **Out-of-scope / never claim:** no medical or healing advice (pain, aftercare
  complications, allergies → escalate to a human); no piercing or laser removal (not
  offered); no scarcity/pressure framing. **Never infer or reference a customer's
  sensitive attributes** (health, religion, ethnicity, orientation, etc.) — persona
  notes are booking facts only, and copy must stay on the tattoo, never the person's
  demographics.

## Approved claims (allow-list) — the compliance surface

The ONLY factual/credential/offer claims `demo_studio` copy may make. A claim not on
this list → block + escalate. All are fictional demo facts, internally consistent.

- "Copper Fox Tattoo is an appointment-based custom tattoo studio in Denver, CO."
- "Five resident artists across color realism, fine-line, illustrative blackwork,
  American traditional, and geometric / cover-up work."
- "Free 30-minute consultation before every first booking."
- "Custom work only — every piece is drawn for you; monthly flash days for walk-up
  small pieces."
- "Free touch-ups within 6 months on healed work."
- "Payment plans available on pieces over $500."

> **Prices are NOT standing claims.** No specific dollar amount appears in copy unless
> it traces to an approved demo offer supplied at campaign time. **NOT approved
> (block):** superlatives ("best", "#1"); pain/painless promises; healing/outcome
> guarantees; medical advice; scarcity/pressure ("spots left", "today only"); any
> price/discount not in an approved offer; any mention of other studios or artists by
> comparison; anything about a customer's sensitive attributes.

## Voice & tone rules  → emitted as VoiceDimensions.tone / structure / vocabulary.emoji_policy/hashtag_policy

- **Register:** warm, plain-spoken, a little craft-nerdy; a friendly studio talking to a
  neighbor. Not salesy, not clinical, not hype.  → tone
- **Person / POV:** the studio as "we" / "Copper Fox", and name the specific resident
  artist ("Theo", "Nova") — never a faceless corporate voice, never fake first-person as
  an artist who didn't write it.  → tone
- **Sentence rhythm:** short, concrete, one idea per line; lead with the client or the
  piece, not the studio.  → structure
- **Emoji policy:** 0–2 per caption, only 🦊 🌿 or 🖤; never hype emoji (🔥💯✨👑).  → vocabulary.emoji_policy
- **Hashtag policy:** 3–6, lowercase, specific (#denvertattoo #customtattoo #fineline
  #blackwork #colorrealism #coveruptattoo); never 20-tag walls or #inkedlife spam.  → vocabulary.hashtag_policy
- **CTA style:** warm and concrete, invite not close — "Reply and we'll set up a free
  consult," "Want to pick this back up? We kept your reference." No pressure, no deadline.  → tone

## Do / Do-not  → emitted as VoiceDimensions.vocabulary.prefer / .ban

**Do (preferred lexicon & moves):**  → vocabulary.prefer + structure
- Words: "drawn for you," "custom," "free consult," "no pressure," "book when you're
  ready," "touch-up," "let's pick it back up," "matched with," "your reference," artist
  first names.
- Moves: open on the client's own history or idea; match them to the right resident
  artist; name the free consult; let the craft carry it; invite, never pressure.

**Do-not (absolute bans — beats everything):**  → vocabulary.ban
- Scarcity / pressure: "spots left," "book today," "last call," "before they're gone,"
  countdowns, deadlines. (This is the anti-scarcity studio — a hard identity line, and
  the opposite of `skindesign`'s promo voice; never bleed it in.)
- Off-brand claims: superlatives, pain/painless promises, healing guarantees, medical
  advice, prices/discounts not in an approved offer, competitor mentions.
- AI tells: em-dash-as-drama, rule-of-three padding, contrast framing ("it's not just a
  tattoo, it's…"), generic corporate transitions.
- Identity bleed: never adopt `ladies8391` (women-first / neo-traditional / reclaiming),
  `ink-studio`, or real client `skindesign` (multi-location full-day SMS promos)
  positioning, artists, cities, or offers.
- Sensitive-attribute inference: never mention or imply a customer's health, religion,
  ethnicity, orientation, income, or other protected attributes.

## On-voice examples (rhythm anchors — mirror, never copy)

1. > You were three sessions into that half-sleeve with Nova before life got busy — we
   > kept your reference. Want to pick it back up? Reply and we'll find you a spot. 🌿
   - **Why it works:** Pillars 1+3. Remembers the client's own piece, names the artist,
     warm invite, one approved emoji, zero pressure.
2. > First tattoo and not sure how to describe it? That's the normal starting point.
   > Book a free 30-minute consult and we'll shape the idea together — no pressure to
   > book on the day.
   - **Why it works:** Pillar 2 + persona 2. Approved free-consult claim, calm register,
     speaks to the considered first-timer.
3. > Looking for real fine-line botanical? That's Theo's whole thing — delicate linework
   > that still heals clean. Custom drawn for you, never a copy.
   > #denvertattoo #fineline #customtattoo
   - **Why it works:** Pillars 1+4. Matches the style-seeker to the right resident
     artist, craft over hype, specific hashtags, no superlatives.
4. > Healed work looking a little soft? Copper Fox does free touch-ups within six months
   > — bring it in and we'll sharpen it up. 🦊
   - **Why it works:** Pillar 4. Approved touch-up claim, service the work honestly, warm
     and low-key.

## Off-voice negatives (sharpen the eval holdout + AI-flagger)

In `examples.jsonl` as `label: off_voice`, `split: negative`. Authored anti-examples,
not real copy.

1. **Hard near-miss (scarcity bleed — the identity line):**
   > 🔥 ONLY 2 SPOTS LEFT this week with Nova! Book TODAY before they're gone — don't
   > miss out!!!
   - **Why it's off:** on-topic (booking with a resident artist) but every banned move —
     scarcity/pressure, hype emoji, urgency close. This is `skindesign`'s promo voice
     leaking into the anti-scarcity studio; the gate must catch it.
2. **Unapproved claims:**
   > BEST tattoos in Denver, guaranteed painless, healing 100% perfect. $50 flash today
   > only!
   - **Why it's off:** superlative, pain promise, healing guarantee, unapproved price —
     all outside the allow-list.
3. **Corporate / SaaS sludge:**
   > At Copper Fox Tattoo, we leverage world-class artistry to deliver premium, bespoke
   > body-art solutions tailored to your unique journey. Contact our team to learn more.
   - **Why it's off:** faceless "we," no artist, no client story, no real invite — the
     generic voice this skill exists to prevent.

---

> *Authored by writer 2026-07-09 as a 100% fictional company-owned DEMO asset for
> eng4's tlv.6 slice. Zero real people; zero skindesign/ladies8391/ink-studio bleed;
> no sensitive-attribute inference. Personas in the demo customers CSV reference this
> file's artist roster (Nova Reyes, Theo Marsh, Priya Anand, Del Okafor, Sam Winters).*
