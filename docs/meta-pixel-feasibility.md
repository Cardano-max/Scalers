# Meta Pixel — feasibility notes (for the Muaraf discussion)

**Source:** client direction, PA meeting 2026-07-11. Jigger asked us to look at
"pixels" before the next meeting — tracking where the audience goes and what they
have in common, to inform targeting. Ateeb to confirm feasibility with Muaraf
first. This doc is the groundwork; the live integration is **not** wired until
that sign-off.

## What the client actually asked for

Two distinct layers, and he was explicit they are different:

1. **Per-lead deep research** (already built) — study ONE known lead: their real
   conversation history + public profiles → a personalized draft.
2. **Pixel / audience commonalities** (this doc) — study the WHOLE audience's
   habits: where they go, what they buy/like ("100 people go to Nike → we know
   the audience leans there"), so targeting can lean into those commonalities.

The Pixel layer is aggregate and anonymized; it does not personalize a single
message. Keeping the two separate is the client's own framing.

## What "pixels" maps to technically

The client's "pixels" is the **Meta Pixel** plus the **Conversions API (CAPI)**:

- **Meta Pixel** — a browser snippet on the client's site/booking pages that fires
  standard + custom events (`PageView`, `ViewContent`, `Search`, `Lead`,
  `InitiateCheckout`, …) with `custom_data` (content category/name, value).
- **Conversions API** — the server-side counterpart (`POST
  /{pixel_id}/events`), the durable path as browser signal degrades (ITP,
  ad-blockers, iOS). This is where audience-commonality events would land.

What Meta actually gives back for "commonalities" is **not** a raw per-person
browsing history (the client's "they went to Nike, then this restaurant" is not
literally exposed). The real, ToS-compliant signals are:

- **Custom Audiences / Lookalikes** built from your own pixel/CRM events;
- **Audience insights** (aggregate demographics/interests of people who fired
  your events) — aggregate, not per-person;
- your **own** `custom_data` (category/value) on events you send.

## Feasibility — the honest read

| Question | Answer |
|---|---|
| Can we fire Pixel/CAPI events from the client's site/booking flow? | **Yes**, if the client owns the pixel + site. Needs `META_PIXEL_ID` + a CAPI access token + event-source setup on their property. |
| Can we get literal "this person visited Nike then a restaurant"? | **No** — Meta does not expose cross-site per-person history to advertisers. We get our own events + aggregate audience insights + audiences to target. |
| Can we compute audience commonalities from OUR events? | **Yes** — aggregate the `custom_data` we send/receive (category, source, declared interests). That is exactly what `studio/meta_pixel.summarize_audience_commonalities` does. |
| Does this need the client's cooperation? | **Yes** — pixel install, Business Manager access, a CAPI token, and a privacy/consent banner on their site. This is the gating dependency to confirm with Muaraf. |
| Data-protection exposure? | Real. Aggregate-only storage on our side (hashed visitor keys, no raw PII), consent banner on the client's site, and a data-processing understanding. PDPA/GDPR apply. |

**Bottom line for the meeting:** the aggregate-commonality version is feasible and
partially scaffolded now (deterministic aggregator + config, disabled by default).
The literal "track each client across the web" version is not something Meta
exposes to advertisers — set that expectation with the client. Confirm with Muaraf
whether the client can/will install the pixel + provide a CAPI token before we
wire the live `POST /events` path.

## What's built now (groundwork only)

- `config.schema.MetaPixelConfig` — per-tenant `[meta_pixel]` (disabled by
  default; `pixel_id`; token via `SecretRef`, never inlined). No live call while
  disabled.
- `studio/meta_pixel.py`:
  - `summarize_audience_commonalities(events)` — pure aggregator over
    Pixel/CAPI-shaped events → top categories/domains/interests by real frequency
    (honest zeros on an empty/signal-less feed).
  - `render_pixel_audience_block(summary)` — a facts-only brief block to bias
    targeting (real counts/shares, so any copy referencing them is grounded).
  - `pixel_settings` / `pixel_enabled` — config readers; the token stays a ref.

## Not built (needs the Muaraf sign-off first)

- The live CAPI client (`POST /{pixel_id}/events`) behind the vetted egress seam
  (`graph.facebook.com` is already on the `facebook` host allowlist).
- Event-source wiring on the client's site + consent banner.
- Custom Audience / Lookalike creation from the aggregated cohorts.
