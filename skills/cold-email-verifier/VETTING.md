# Vetting record — cold-email-verifier (CustomerAcq-1mk.7)

Growth-side record for the 1mk.1 gate. **sec owns the S1 sign-off** (SUBMITTED).
Canonical row: `docs/skills/registry.md`. **ELIGIBLE ≠ IN USE**; release-gated by
bead 439 (the verifier is a gate, not a sender, but it serves the 439-held engine).

| Field | Value |
|-------|-------|
| Skill | `cold-email-verifier` (verifier-only) |
| Upstream pattern | "cold-email-verifier" (r/ClaudeAI 20-skills list) — verify-half only |
| Pinned commit | `ORIGINAL (no upstream code vendored)` — sec resolves/verifies real 40-hex SHA at fetch |
| Skill type | Pattern-only re-authoring; deterministic in-house validator |
| Our-format path | `skills/cold-email-verifier/` + `engine/outreach/verifier.py` |
| sec sign-off (S1) | **SUBMITTED — pending sec verification** |
| Eval-gate status | **PENDING-on-gold-set** (`evals/gold/outreach-smoke.jsonl`; calibration = rvy.7/.8) |
| Status | **HELD** — sec S1 + eval-gate + operator adopt-approval pending |

## 4-step gate

1. **READ** — pattern source read. The upstream "guess + enrich + autonomous CSV"
   behavior is **data-broker enrichment** (REJECTED class). No upstream script
   vendored.
2. **STRIP** — the **guess/enrich/auto-CSV** capability is **not taken**; only the
   deterministic *verify* checks are re-authored. No network in the deterministic
   core (the live MX probe is a separate eng seam with its own resolver/TLS); no
   broker call; no send; no money surface.
3. **RE-AUTHOR + PIN** — original deterministic validator
   (`engine/outreach/verifier.py`): syntax, disposable, role, shape, optional MX
   seam. Reproducible, hermetic. Pin required (sec fills SHA at fetch).
4. **EVAL-GATE** — `evals/gold/outreach-smoke.jsonl` (deliverability rows);
   replayed by `test_outreach_gold_smoke.py`. Holdout/calibration = rvy.7/.8.
   **PENDING.**

## What was stripped

The guess/enrich/autonomous-CSV (broker-enrichment) half — **not adopted**. Any
send — not present. Residual surface: **none** in the deterministic core (pure
regex + set membership). The optional live MX probe is an explicit, separately
vetted eng seam. Pre-strip max severity: **HIGH** (broker enrichment).
