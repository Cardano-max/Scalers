# Tattoo-studio outreach (retarget) — outreach-sequence-builder

The upstream pattern targeted B2B/DevRel cold outreach across email/LinkedIn/phone.
We constrain it to **email**, **consent-aware**, and retarget to a tattoo studio's
real outreach contexts. Method is the asset; the audience + cadence rules are ours.

## Who a tattoo studio actually emails (consent-aware, not spray)

| Audience | Basis | Angle |
|---|---|---|
| Past inquiries that never booked | prior inquiry | warm follow-up: new flash / guest-spot dates |
| Event / convention signups | opt-in at event | "great meeting you — here's how to book" |
| Local partners (barbershops, boutiques, studios) | B2B partnership | collab / referral, flash pop-up |
| Lapsed clients (win-back) | prior client | new styles, touch-up reminders |

No anonymous list-buying. Every prospect has a `consent_basis`; suppression is
checked first; honor opt-out ≤2 days.

## Cadence (spec §5)

- 4 touches, widening gaps: **day 0 / +3 / +5 / +7**. Purpose per touch: intro →
  value-add → soft-CTA → break-up.
- Warmup ramp per inbox/day: ~8 (wk1) → 18 → 28 → 40 (wk4); 25 on consumer Gmail.
- RFC 8058 one-click unsubscribe on every touch. Hard-stop on reply/bounce/unsub.

## Personalization (relevant, never creepy)

Reference ≤2 **allowed** signals/touch — public studio style, a past inquiry
topic, an event attended. NEVER home address, family, health, finances,
employer-internal, or real-time location (the guard strips these). Over-personalized
= a fail.

## Deliverability protection (growth-monitored)

Suppression-first + verification + caps keep spam complaints <0.10% (operate
<0.08%) and bounce <2%, protecting the sending domain (SPF/DKIM/DMARC).
