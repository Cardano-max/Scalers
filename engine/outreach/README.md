# Outreach engine (bead 1mk.7)

Suppression-first, deliverability-verified, capped, **escalate-only** cold-email
outreach for a tattoo studio. The skills are prompt-only; the rules below are
**deterministic code** so they cannot be prompted away.

## Pipeline (`OutreachPolicy.plan`)

1. **suppression-first** (`suppression.py`) — do-not-contact checked before all else
2. **hard-stop** — reply/bounce/unsubscribe/complaint halts the sequence
3. **deliverability verify** (`verifier.py`) — undeliverable blocked, risky escalates
4. **over-personalization guard** (`personalization.py`) — creepy signals stripped, ≤2 refs/touch
5. **capped sequence** (`sequence.py`) — 4 touches at day 0/+3/+5/+7, warmup-aware cap, RFC-8058 unsubscribe every touch
6. **route to review** — `Disposition.ESCALATE`, `routed_to="review"`; `plan.will_send` is always False (bead **439** hold)

`OutreachPlan` is **PII-free** — the prospect is referenced by a salted hash, never the raw email.

## Ownership boundaries

| Concern | Owner |
|---|---|
| Sequence structure, caps, spacing, suppression, verification, hard-stop, creepy guard, gating | **growth** (this package) |
| Per-touch **copy** (hook/CTA/body), on-voice | **writer** — copywriter cell (1mk.5) grounded in brand-voice (S2), checked by S3 AI-flagger + jury |
| Live MX/SMTP probe + the Gmail **send connector** (exactly-once side-effect boundary) | **eng** (seams: `DeliverabilityVerifier(mx_check=…)`, `sideeffects/`) |

The policy emits a plan + per-touch personalization brief; the writer fills the
copy; eng's connector would send **only** once 439 lifts.

## Release gates (no real sends until ALL pass)

- **sec S1** sign-off (1mk.1 vetting; rows SUBMITTED in `docs/skills/registry.md`)
- **eval** — `evals/gold/outreach-smoke.jsonl` smoke (here) → rvy.7 (Inspect suite) + rvy.8 (calibration: ECE≤0.05, P/R≥0.95, brand-voice≥90%) on a real outreach gold set
- **bead 439** — per-channel autonomy stays MANUAL/escalate until the above are green for the outreach engine/channel

DMs always escalate (never in scope for auto-send).
