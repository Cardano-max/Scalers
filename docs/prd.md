# marketing PRD

The "why" document. Problem statement, users, success criteria, journeys. Hard cap: 5000 lines.

> Codename: **Scalers**. Internal, single-client, engine-first agentic social-media marketing system.
> Canonical alignment: `docs/stack-decision.md` (stack), `super/scalers-backend-plan.md` (build plan),
> Scalers `.planning/{PROJECT,REQUIREMENTS,ROADMAP}.md` (scope). This doc owns the WHY only — not the HOW.

---

## 1. Problem Statement

### 1.1 The Problem

Marketing a service business across Instagram, Facebook, and cold email is high-volume, repetitive,
and unforgiving. To keep a single client growing, someone has to research angles, write on-brand posts,
publish on cadence, run personalized cold outreach, and answer every comment and DM — every day, without
drifting off-voice and without tripping platform or anti-spam rules. One person cannot sustain that volume
at quality; a small team burns most of its hours on the repetitive 90% and still answers slowly.

The obvious fix — "just automate it" — is exactly where this gets dangerous. Naive automation fires
off-brand or unsafe content, double-posts on a retry, auto-DMs people outside the platform's allowed
window, and blows past send caps until the account is throttled or banned and the sending domain is
burned. Each of those is hard to undo and damages the client's reputation. The market is full of
"AI posting" tools that optimize for volume and have no safety net; none of them are trustworthy enough
to leave running unattended on a real client's accounts.

What's missing is **marketing autonomy you can actually trust**: a system that does the repetitive work
itself, escalates only the genuinely uncertain decisions to a human, **never** sends off-brand or unsafe
content without sign-off, respects every platform limit, and can explain — for every single action — why
it did what it did.

### 1.2 Why Now

- **The models are finally good enough** to draft on-voice content and judge it, but only inside a
  disciplined harness — raw LLM output is too unreliable to publish unattended.
- **Platform rules tightened.** Instagram DM automation is now restricted to the 24-hour window the user
  opens; comment automation and publish volume are capped by account trust. A 2026 system must enforce
  these in code or lose API access. That makes the "auto comments, human DMs" split a requirement, not a
  preference.
- **A single real client exists now** (a tattoo studio) who needs this. Building engine-first for one
  client lets us prove reliable, auditable autonomy before generalizing — the niche lives only in backend
  config, so the same engine serves the next client without a rewrite.

---

## 2. User

### 2.1 Primary User

**The Operator** — the single internal person who runs Scalers on behalf of the client. Technically capable
and marketing-literate, but time-constrained: their job is to supervise an engine doing the bulk of the work,
not to write every post and reply themselves. They live in the **Operator Console** (a locked, generic,
professional web app — tattoo-agnostic by design) and they want to:

- Trust the engine to handle the routine high-confidence work automatically.
- Be pulled in *only* for the uncertain few — and when pulled in, see exactly why (confidence, jury verdict,
  which gates passed/failed) so they can approve, edit, regenerate, or reject in seconds.
- Know nothing unsafe, off-brand, or rule-breaking ever went out without their say-so.
- Steer the system in plain language and dial autonomy up or down per channel as their trust grows.

### 2.2 Secondary Users (Future)

- **Additional clients / tenants.** The engine is single-client now but generic underneath; a second client
  is onboarded by adding a backend config "pack", not by changing the product. (Out of scope for MVP.)
- **The end client** (e.g. the studio owner) — the beneficiary of the marketing, not a hands-on user. They
  see results (reach, replies, leads), not the console.

---

## 3. Success Criteria

### 3.1 Core Success

This worked if, for one real client, **the engine runs the day-to-day marketing and the operator only
touches the uncertain few — with zero unsafe or off-brand sends and a full audit trail.** Concretely:

1. **It does the work.** Organic IG/FB posts, cold email outreach, and comment/DM engagement all run from
   the engine on cadence, not by hand.
2. **It escalates, not floods.** The operator reviews only the low-confidence / borderline actions; the
   high-confidence majority flows automatically (within the per-channel autonomy dial they set).
3. **It never fires unsafe.** No off-brand, unsafe, or policy-violating content is sent without operator
   sign-off; comments may auto-reply, **DMs always route to a human**.
4. **It's exactly-once and auditable.** No double IG post and no double Gmail send under any retry/crash;
   every action carries its confidence, jury verdict, gates, and idempotency key, visible in the console.
5. **It respects the platforms.** Publish/DM/send caps, the IG 24-hour DM window, and email deliverability
   rules are honored in code so the client's accounts and sending domain stay healthy.

### 3.2 Measurable Checks

Observable, testable bars (instrumented; full thresholds owned by spec/eval):

- **Brand-voice fidelity ≥ 90%** on a blind holdout of posts, rated by ≥2 humans (agreement κ ≥ 0.6).
- **Classification/extraction precision & recall ≥ 0.95** vs a labeled gold set, per cell.
- **Reply safety: 0** policy violations on a red-team set; **< 15%** of auto-drafts need a human edit.
- **Exactly-once proven:** a forced crash/retry produces exactly one side effect (test-verified).
- **Email health:** spam-complaint rate **< 0.10%**, plus an inbox-placement target and bounce ceiling.
- **Confidence is honest:** calibration error **ECE ≤ 0.05** on the gold set (the score the operator trusts
  reflects reality).
- **Every action is explainable:** for any sent/queued action, the console shows confidence, jury
  dimensions, gate results, and idempotency key.

---

## 4. Non-Goals

These are deliberately *not* what Scalers is, to keep scope honest:

- **Not a booking system.** Scalers does not manage appointments, calendars, or a booking loop. (This was
  context about the client, never part of this system — and must not be reintroduced.)
- **Not a self-serve SaaS.** No multi-tenant onboarding, billing, or sign-up flow. Engine-first, one client.
- **Not a paid-ads manager.** Running/optimizing paid Meta ad spend is a possible later module, not core.
  (We *do* read the free Meta Ad Library for competitor research — that's research input, not ad management.)
- **Not a tattoo app.** The frontend is generic and professional; the niche lives only in backend config.
- **Not a content firehose.** Volume is never the goal; trustworthy, on-brand, auditable autonomy is.

---

## 5. User Journeys

Concrete operator scenarios. (Screen names map to the locked Operator Console: Overview, Review queue,
Live feed, Runs, Command.)

### Journey A — Morning check-in (the happy path)
1. Operator opens the **Overview**. KPI cards show autonomy % today, review-queue count, outreach sent,
   complaint rate, and posting/engagement health — all green, queue is small.
2. They glance at **Live feed**: the engine published the scheduled posts, auto-replied to routine comments,
   and sent the day's capped outreach batch — each event tagged with its worker and severity.
3. Nothing needs them. They close the tab. *Expected outcome: the engine ran the day; the operator spent
   two minutes confirming it.*

### Journey B — Reviewing an escalation
1. A badge on **Review queue** shows a new item: an outreach draft flagged **weak personalization**,
   confidence 0.78 below the 0.85 threshold.
2. The operator opens it and sees the autonomy-decision card: the draft, the jury's per-dimension scores
   (voice / safety / appropriateness), the gates that passed (suppression, rate cap, PII), and the reason
   it escalated.
3. They tighten one sentence inline and **Approve** — or hit **Regenerate** for a fresh attempt, or
   **Reject**. On approve, the engine sends it; the item leaves the queue. *Expected outcome: a borderline
   action got a human decision in under a minute, with full context.*

### Journey C — A comment vs. a DM
1. A follower comments on a post. The engine classifies it as routine-positive, scores it high, and — within
   the comment autonomy dial — **auto-replies**. It appears in **Live feed** as an auto action.
2. The same follower sends a **DM**. Per operator policy, DMs **never** auto-send: the engine drafts a reply
   and routes it to **Review queue** for a human, respecting the IG 24-hour-window rules. *Expected outcome:
   public comments are handled at speed; private messages always get a human.*

### Journey D — Steering by command
1. The operator opens **Command** and types: *"Pause Instagram posting until Friday and focus outreach on
   studios in Portland."*
2. The harness streams back a confirmation of what it changed. The engine state and autonomy reflect it; the
   change shows in **Runs** / **Live feed**. *Expected outcome: the operator redirects the engine in plain
   language without touching config.*

### Journey E — Auditing a run
1. Something looks off — a run shows **Failed** in **Runs**.
2. The operator opens the run detail: the full trajectory (each step + state), the auto-vs-review split,
   retries, and the idempotency key. They see it failed at the publish step on a rate cap, retried, and
   no double-post occurred. *Expected outcome: every run is explainable after the fact; the operator can
   trust that failures were safe.*

---

## 6. Risks

| Risk | Why it matters | Mitigation (WHAT, not HOW) |
|------|----------------|----------------------------|
| Off-brand / unsafe content sent | Destroys client trust instantly | Nothing auto-sends without passing jury + gates + safety; DMs always human; per-channel autonomy dial |
| Double post / double send | Duplicate publishes and emails are visible, embarrassing, unrecoverable | Exactly-once guarantee proven by test under forced crash/retry |
| Platform ban / API loss | IG/FB/Gmail access is the whole product; losing it stops everything | Enforce publish/DM/send caps, IG 24h-DM window, official APIs only — in code, not by hope |
| Burned sending domain | Cold email on a bad domain kills deliverability permanently | Separate sending domain, warmup ramp, low per-inbox volume, unsubscribe + suppression, complaint ceiling |
| Brand-voice drift over time | Slow quality decay erodes the core value silently | Voice grounded in the client's past content; fidelity measured on a holdout; feedback loop with drift control |
| Overconfident automation | A miscalibrated score auto-sends something it shouldn't | Confidence is computed and calibrated (ECE ≤ 0.05), never self-reported; gold set gates promotion |
| Meta app review delay (2–4 wks) | Blocks real publish/comment scopes | Treat as a known long pole; start review day one; keep it off the critical path |
| Scope creep (esp. booking) | Reintroducing removed scope stalls the engine | Non-goals are explicit; all scope changes go through the operator |

---

## 7. Scope Boundaries

### 7.1 MVP Scope (Build This)
- Three engines on one deterministic harness: **organic IG/FB posting**, **Gmail cold outreach**,
  **comment/DM engagement** (comments may auto-reply; **DMs route to human**).
- A shared **deep-research** capability (competitor / winning-pattern mining), using the **free Meta Ad
  Library** for competitor ads.
- **Autonomy with a safety net:** cross-family jury + calibrated confidence + deterministic gates + safety
  classifier + per-channel autonomy dial.
- The locked, generic **Operator Console** wired to the live engine (Overview, Review queue, Live feed,
  Runs, Command), with real-time updates and operator actions (approve / edit / regenerate / reject,
  pause / resume, set autonomy, command).
- **Exactly-once side effects**, a full per-action audit trail, and an **eval spine + gold set** gating
  quality.
- Single client; the tattoo niche lives only in **backend per-tenant config / "packs"**.

### 7.2 Post-MVP (Build Later, If Needed)
- Paid-ads management module (official Meta Ads, behind the same safety gate).
- Multi-tenant fan-out and multiple operator sessions.
- A self-hosted brand-voice adapter, if prompting alone caps fidelity.
- **Reddit** as a research source (out of the MVP brain — current API terms make commercial use costly;
  revisit behind the pluggable research adapter).

### 7.3 Never Build
- A **booking system / booking loop** — explicitly removed; not this system.
- A **tattoo-specific frontend** — the console stays generic; the niche is backend config.
- **Private-API / scraping** social tooling — ban risk; official Graph API only.
- **AWS / cloud infrastructure** — local Docker + Cloudflare tunnel only.

---
*Owner: pm. Last updated: 2026-06-28. Aligned to docs/stack-decision.md (canonical) + super/scalers-backend-plan.md + Scalers .planning/{PROJECT,REQUIREMENTS,ROADMAP}.md.*
