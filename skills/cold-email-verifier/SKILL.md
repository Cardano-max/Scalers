---
name: cold-email-verifier
description: Use before any outreach send to verify an address is deliverable and
  safe to contact. Deterministic checks — syntax, disposable domains, role
  accounts, shape heuristics, optional live MX — returning deliverable / risky /
  undeliverable. It NEVER guesses, enriches, or buys data, and it NEVER sends.
  Trigger words: verify email, deliverability, bounce risk, disposable, role
  account, validate address, clean list.
license: pattern-only re-authoring (verifier-only) — see NOTICE
pinned: ORIGINAL  # no upstream code vendored (pattern-only)
---

# Cold Email Verifier (deterministic, in-house)

## Overview

This skill gates outreach on **deliverability**, protecting the sending domain.
The upstream `cold-email-verifier` "guesses, enriches, and verifies emails from a
CSV autonomously" — we **keep only the verify half and strip the guess + enrich +
autonomous-CSV behavior** (that half is data-broker enrichment — apollo / hunter /
zoominfo class, REJECTED in the registry). What remains is a **deterministic
in-house validator** (`engine/outreach/verifier.py`), the 1mk.3 move: a skill
becomes pure, reproducible code, not an off-the-shelf classifier or a broker call.

> "cold-email-verifier: guesses, enriches, and verifies emails from a CSV
> autonomously" — Sam_Tech1, r/ClaudeAI (verbatim, winning-strategies-kb.md —
> the upstream; we adopt the **verify** part only)

## When to use

Load before planning/advancing an outreach sequence, on every prospect, every
time (spec §5: suppression + verification before every send). Pairs with
`outreach-sequence-builder` (it runs verification as gate #2, after suppression).

## What it checks (deterministic; no network, no send, no enrichment)

| Check | Verdict |
|---|---|
| Malformed syntax (RFC-ish) | **undeliverable** |
| Disposable / throwaway domain | **undeliverable** |
| Role account (info@, sales@, …) | **risky** (escalate, never auto) |
| Unusual local-part shape | **risky** |
| Clean syntax + shape | **deliverable** (subject to live MX) |

A live **MX/SMTP probe** can downgrade `deliverable`→`undeliverable` — that is an
eng seam (`mx_check`), with its own resolver + TLS, not part of this deterministic
core. No address is ever guessed or enriched; no list is auto-processed.

## Method

- Verify each prospect's address; **block undeliverable**, **escalate risky** with
  a warning. Never auto-send on any verdict (439 hold).
- Feed the verdict into the outreach plan; growth monitors aggregate bounce
  (<2%) + complaint (<0.10%) rates to protect the domain.

## Edge cases

- Disposable/role/unverifiable → handled above. A list with many risky/undeliverable
  rows is a signal to fix the source, not to push send.
- It does **not** enrich a missing address — if there's no valid email, there's no
  outreach (consent-aware intake upstream).

## Output contract

Returns a `VerificationVerdict` (`status`, `reasons`, `can_send`). Consumed by the
outreach policy; never sends, never enriches, never writes a broker query.
