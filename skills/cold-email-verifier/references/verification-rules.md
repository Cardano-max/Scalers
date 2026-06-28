# Verification rules — cold-email-verifier

Deterministic, hermetic. Source of truth: `engine/outreach/verifier.py`.

| Rule | Verdict | Why |
|---|---|---|
| Fails RFC-ish syntax | `undeliverable` | can't deliver a malformed address |
| Disposable/throwaway domain | `undeliverable` | throwaway = no real recipient; bounce/complaint risk |
| Role account (`info@`, `sales@`, …) | `risky` | complaint-prone shared inbox → escalate, never auto |
| Unusual local-part (≥4 dots / >40 chars) | `risky` | shape anomaly → human look |
| Clean syntax + shape | `deliverable` | subject to a live MX downgrade |

## Live MX seam (eng-owned)

`DeliverabilityVerifier(mx_check=…)` accepts a live MX/SMTP probe that can downgrade
`deliverable → undeliverable` (no MX record). The probe MUST use its own
resolver + TLS and respect rate limits; it is vetted separately. The deterministic
core never depends on it (stays hermetic in tests).

## Hard line (skills-dos-donts.md)

- **No guessing/enriching** an address (data-broker class — REJECTED).
- **No autonomous CSV processing.** Per-prospect, in-policy only.
- **No sending.** Verification is a gate; sends are the 439-held harness boundary.
