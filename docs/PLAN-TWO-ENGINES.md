# Plan Spec — Social Growth Engine + Warm Lead Reactivation Engine

_Operator-approved direction (2026-07-10). This maps the operator's idea + the
deep-research findings onto what Scalers already has, and sequences what to
build. Prime directives: evidence-based personalization only (no sensitive-trait
inference from photos), everything HELD behind approval, every output traceable
to its sources._

## Verdict on the idea

The idea is right, with the corrections already adopted: the winning system is
**first-party conversation history + campaign history + artist memory + approved
public signals + competitor creative intelligence → evidence-based personalized
campaigns** — not "guess the person from their photos". Competitor content is
*inspiration to mold* (structure/hook/CTA patterns), never copied; our artwork,
brand voice, and offers are the actual creative.

## Engine 1 — Warm Lead Reactivation (email/SMS)

Flow (most of this EXISTS today):

    Voice/Chat Supervisor
    → upload conversation CSV  ................ BUILT (conversation_import.py:
        speaker+text CSVs auto-detected; verbatim turns → lead_conversations;
        customers upserted; explicit 'stop' → sms_opt_in=false at import)
    → per-lead objection read ................. EXISTS (analyst/psych step reads
        the REAL thread via conversation_leads; 'insufficient-signal' honest)
    → customer dossier ........................ EXISTS (studio/dossier.py)
    → approved public enrichment .............. EXISTS, consent-gated (customer-
        provided handles only; protected-traits ban stays)
    → strategy per lead + artwork match ....... EXISTS (per-lead strategy, top-4
        artwork pause, operator/client picks)
    → personalized draft + evidence ........... EXISTS (no-fabrication guards)
    → Review Queue only ....................... EXISTS (HELD, TEST-MODE gate)

To extract per the operator's list (wanted tattoo type, budget/timing/payment/
trust issue, last contact status, next-best-offer): the analyst prompt already
classifies objections from the real thread; EXTEND its taxonomy with
`trust_concern` (e.g. the Amanda Kuhl double-reschedule → refund case) and
`blocked_by_prereq` (e.g. Lauren: laser removal required first). Angle map:
price → payment plans; timing → flexible slots; trust → direct-artist,
no-reschedule guarantee, manager contact; quiet → low-pressure check-in;
prereq → helpful next-step (laser partner info), NOT a sell.

## Engine 2 — Social Growth (Instagram/Facebook via Meta Business Suite)

    Voice Supervisor → channel: instagram|facebook
    → load artist memory + 6-month performance   [artist memory EXISTS; perf
        import = NEW: appointments/campaign CSVs → outcomes tables]
    → competitor creative intelligence           [NEW — see below]
    → deep social research                       [EXISTS: 3 cited-only angles
        incl. reddit-community]
    → mold best pattern onto OUR assets          [brand_patterns + broll blocks
        EXIST; molding step = NEW prompt stage: structure from competitor,
        creative from our library, wording from brand voice, offer from
        substantiated codes]
    → stage for approval                         [EXISTS]
    → schedule / manual trigger                  [NEW: scheduled_job_runs exists;
        add per-post schedule + voice tool 'schedule_post'; Meta publish blocked
        on the page token]

Competitor Creative Intelligence (NEW module `studio/competitor_intel.py`):
- Inputs: competitor handles/niche (operator-provided), fetched via official
  APIs/analytics exports where allowed — never scraping in violation of ToS.
- Score 10–20 posts/reels 0–10 on: engagement rate, comments, views,
  shares/saves when available, niche similarity, artist-style similarity
  (VLM tags vs our library), CTA strength, hook strength, brand fit, recency.
- Persist to **Competitor Memory** (new table: handle, url, caption, visual
  tags, scores, why-it-worked) so patterns compound across campaigns.
- Output: the ONE best pattern + its deconstruction (hook/structure/emotional
  angle/CTA/visual pattern) → fed to the molding stage.

## The five memories (state of each)

| Memory | Status |
|---|---|
| Artist (profile, styles, artwork+video embeddings, campaigns, voice) | BUILT (Keebs: 16 pieces incl. b-roll, style profile, proven campaign voice) |
| Customer (conversation, objection, artist interest, response, opt-outs) | BUILT + conversation import NEW today |
| Campaign (goal/channel/creative/perf/what worked) | PARTIAL — examples+patterns exist; outcome/performance feedback loop still open |
| Competitor (posts, scores, hook types, why-it-worked) | NEW — to build with Engine 2 |
| Brand (tone, do/don't, approved offers, compliance) | BUILT (brand docs, offer substantiation, protected-traits ban, opt-out rules) |

Storage stays the current hybrid: Postgres facts + pgvector semantic +
artifact store for media + graph-ish links (customer→artist→campaign→artwork
already navigable via ids).

## Build order

1. **DONE today**: conversation-CSV intake (verbatim, opt-out capture, cohort
   attach) — unblocks the 20-conversation Keebs POC immediately.
2. Objection-taxonomy extension (trust_concern, blocked_by_prereq) + angle map.
3. Appointments/performance import (the a_1/a_2 style exports → real history
   per customer: last visit, deposits, styles — feeds dossiers + "6-month
   performance" reads).
4. Competitor Intelligence module + Competitor Memory + molding stage in the
   IG pipeline (behind the existing brand_patterns/broll blocks).
5. Scheduling: per-post schedule + manual trigger via voice; Meta publish once
   the page token lands (IG first, FB same pipeline with tone variant).
6. Campaign Intelligence dashboard (best campaigns/patterns/segments + why) —
   the "executive brain" reads all five memories.
7. Only after reliability: weekly auto-suggestions (still approve-first).

## Non-negotiables carried through

- No sensitive-trait inference (age/gender/identity from photos). Personalize
  from stated facts + consented public signals only.
- Competitor patterns are inspiration; assets/wording/offers are always ours.
- Approve-first everywhere; TEST-MODE gate for the real tenant; opt-outs
  travel with the data (import-time capture).
- Every draft carries its evidence trail (conversation rows, artwork ids,
  competitor pattern id, brand rules used).
