# Outreach suppression + deliverability mechanism (design, build-ready)

**Bead:** CustomerAcq-1mk.7 (outreach engine). **Author:** growth. **Status:**
DESIGN for eng to build. Turns the deterministic in-memory core already shipped
(PR #47, `engine/outreach/`) into a **persistent, event-fed** production
mechanism: a suppression-first list, deliverability verification, and a
cap/throttle policy — all upstream of the exactly-once side-effect boundary, all
under the **bead 439** no-auto-send hold.

> Already built (the deterministic core this wires to): `SuppressionGate`
> (`suppression.py`), `DeliverabilityVerifier` (`verifier.py`), `SequencePlanner`
> caps/warmup (`sequence.py`), `OutreachPolicy` (`policy.py`). This doc adds the
> **persistence + ingestion + live-probe** layers around them.

---

## 0. Where it sits (gate order — never violated)

Per send candidate, in order, BEFORE any provider call:

```
suppression-first  →  hard-stop check  →  deliverability verify  →  cap/throttle
        ↓                    ↓                     ↓                      ↓
   skip if listed      halt sequence        block/escalate         defer if over cap
                                  ↓ (all pass)
                       OutreachPolicy.plan → route=REVIEW (439) → [human approve]
                                  ↓ (only after 439 lifts + approval)
                       idempotency_key(tenant,'outreach',ref,touch) → outbox → send
```

The side-effect boundary (`sideeffects/`) is the **only** way a send is enqueued;
suppression/verify/throttle are the gates the candidate clears to get there.

---

## 1. Suppression-first list

### 1.1 Data model

```sql
CREATE TABLE suppression_entry (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     text NOT NULL,
    -- exactly one of email / domain / ref identifies the target:
    email         text,                  -- normalized lower(email)
    domain        text,                  -- '@'-stripped, for domain-wide blocks
    prospect_ref  text,                  -- salted hash (PII-free lists)
    reason        text NOT NULL CHECK (reason IN
                  ('unsubscribe','bounce','complaint','manual','global','role_block')),
    source        text NOT NULL,         -- 'rfc8058' | 'smtp_bounce' | 'fbl' | 'operator' | 'seed_csv'
    scope         text NOT NULL DEFAULT 'TENANT' CHECK (scope IN ('TENANT','GLOBAL')),
    added_at      timestamptz NOT NULL DEFAULT now(),
    evidence      jsonb DEFAULT '{}',    -- bounce code / complaint headers (PII-redacted)
    CONSTRAINT suppression_target CHECK (num_nonnulls(email,domain,prospect_ref) = 1),
    CONSTRAINT suppression_natural_key UNIQUE (tenant_id, reason, email, domain, prospect_ref)
);
CREATE INDEX suppression_lookup ON suppression_entry (tenant_id, email);
CREATE INDEX suppression_domain ON suppression_entry (tenant_id, domain);
CREATE INDEX suppression_ref    ON suppression_entry (tenant_id, prospect_ref);
```

- **GLOBAL scope** rows (e.g. a known spam-trap domain) apply across tenants
  (read like the eval-KB global metrics). TENANT rows are tenant-isolated (RLS,
  same pattern as `gold_example`).
- Idempotent: re-ingesting the same (tenant, reason, target) is a no-op
  (`ON CONFLICT DO NOTHING`).
- **Never deleted on opt-out** — an unsubscribe is permanent; a removal is an
  explicit operator action, audited.

### 1.2 The check (before every send)

`SuppressionStore.check(tenant_id, prospect) -> SuppressionResult` — one query
across email + domain + ref (the in-memory `SuppressionGate` is the same logic;
the store is its DB-backed form). It is **gate #1** in `OutreachPolicy` — a hit
returns `SKIP_SUPPRESSED`, **no verification, no sequence, no send**.

### 1.3 Ingestion (how entries get IN)

| Source | Trigger | reason | Path |
|---|---|---|---|
| **RFC-8058 one-click unsubscribe** | recipient clicks unsubscribe | `unsubscribe` | unsubscribe endpoint → `suppress(...)` (honor ≤2 days; we apply immediately) |
| **SMTP hard bounce** | bounce notification / Gmail API | `bounce` | feedback poller/webhook → `suppress(...)` + hard-stop the sequence |
| **Feedback-loop complaint (FBL)** | mailbox provider complaint | `complaint` | FBL webhook → `suppress(...)` + hard-stop |
| **Manual / do-not-contact** | operator | `manual` | console action |
| **Seed list** | tenant onboarding | `global`/`manual` | `minio://suppression/<tenant>.csv` (pack `[suppression].source`) loaded at boot + on change |

Ingestion reuses the existing inbound-event seam (`sideeffects/capture.py` already
takes webhook/poll events, PII-redacted) — bounce/complaint/unsub are new event
kinds routed to `suppress(...)`. Each is idempotent (natural key) so retries/
re-delivery never double-write.

### 1.4 Eng builds

```python
class SuppressionStore:
    def check(self, tenant_id: str, prospect: Prospect) -> SuppressionResult: ...
    def suppress(self, tenant_id: str, *, email=None, domain=None, ref=None,
                 reason: str, source: str, evidence: dict | None = None) -> None: ...
    def load_seed(self, tenant_id: str, csv_uri: str) -> int: ...   # minio seed
```

---

## 2. Deliverability verification (cold-email-verifier path)

### 2.1 Deterministic core (already built)

`DeliverabilityVerifier.verify(email) -> VerificationVerdict` (syntax / disposable
/ role / shape) → `deliverable` | `risky` | `undeliverable`. **No enrichment, no
guessing, no send** (broker half stripped). Gate #3 in the policy:
`undeliverable` → BLOCK, `risky` → escalate + warn.

### 2.2 Live MX/SMTP probe (eng seam)

`DeliverabilityVerifier(mx_check=callable)` — the live probe downgrades
`deliverable → undeliverable` when there is no MX. Eng builds `mx_check(domain)`:

- own resolver, **TLS where applicable**, timeout-bounded, **rate-limited**
  (reuse the `RateLimiter` pattern from `research/safety.py`);
- **never** an SMTP send — at most a `RCPT TO` probe against providers that allow
  it, otherwise MX-record presence only;
- result **cached** (see 2.3) so we don't re-probe every touch.

### 2.3 Verdict cache + re-verification

```sql
CREATE TABLE deliverability_verdict (
    tenant_id   text NOT NULL,
    email       text NOT NULL,
    status      text NOT NULL,         -- deliverable | risky | undeliverable
    reasons     jsonb DEFAULT '[]',
    checked_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, email)
);
```

- Cache verdicts; **re-verify if older than N days** (default 30) or after a bounce.
- A bounce on a previously-`deliverable` address → flip to `undeliverable` +
  suppress (`bounce`). Verdicts feed the bounce-rate monitor (§3.4).

---

## 3. Cap / throttle policy

### 3.1 Warmup ramp + steady caps (already coded, spec §5)

Per inbox/day: ~8 (wk1) → 18 → 28 → 40 (wk4); steady 40 (Workspace) / 25
(consumer). `cap_per_inbox_day(week, consumer)` is built; this section makes the
**counters persistent + enforced**.

### 3.2 Counters (persistent, per inbox)

```sql
CREATE TABLE outreach_send_counter (
    tenant_id   text NOT NULL,
    inbox       text NOT NULL,         -- sending identity
    window_day  date NOT NULL,
    sent_count  int  NOT NULL DEFAULT 0,
    hour_bucket smallint,              -- optional per-hour sub-cap
    PRIMARY KEY (tenant_id, inbox, window_day)
);
```

- Incremented **only** when the side-effect boundary settles a send `SENT` (not on
  enqueue) so retries don't double-count — read the count off the same exactly-once
  ledger that guarantees one send per key.
- **Throttle gate (#4):** if `sent_today >= cap_per_inbox_day(week)` → the
  candidate is **deferred to the next window**, not dropped (re-queued by the
  scheduler), and flagged.

### 3.3 Spacing + rotation

- Touch spacing day 0/+3/+5/+7 is the sequence; **send-time spacing** within a day
  jitters sends (no burst) and respects `[schedule].quiet_hours` + timezone.
- Multi-inbox: round-robin across the tenant's sending identities so no single
  inbox exceeds its cap; warmup is per inbox.

### 3.4 Threshold monitor + auto-revert (growth-owned)

- Track rolling **bounce rate** (<2%) and **spam-complaint rate** (<0.10%, operate
  <0.08%) per inbox from the verdict/complaint feeds.
- **Breach → auto-revert that inbox/channel to MANUAL** and alert the operator
  (this is the per-channel autonomy auto-revert already required by bead 439's
  edge cases). Outreach stays paused for that inbox until the rate recovers.

---

## 4. Composition + safety

- All four gates run in `OutreachPolicy.plan` (already the composition point); this
  design swaps the in-memory `SuppressionGate`/counters for the DB-backed
  `SuppressionStore`/`outreach_send_counter`, and injects the live `mx_check`.
- **439 hold:** even when all gates pass, `route=REVIEW`; nothing sends until 439
  lifts (rvy.7+rvy.8 green for outreach) AND a human approves. The send itself goes
  through `idempotency_key(tenant,'outreach',prospect_ref,touch_id)` → `outbox`
  (exactly-once; no double-send across retries/crashes).
- **PII:** lists/counters key on normalized email or the salted `prospect_ref`;
  plans/logs use `prospect_ref` only; bounce/complaint evidence is PII-redacted on
  ingest (existing `capture.py` redaction).

---

## 5. Edge cases

- **Suppressed mid-sequence** (unsub on touch 2) → hard-stop, remaining touches
  cancelled, address suppressed.
- **Bounce after send** → suppress + flip verdict + count toward bounce-rate.
- **Domain-wide block** → one `domain` row suppresses all addresses at it.
- **Seed CSV malformed row** → skip the row + log, don't fail the load.
- **Counter race** (two workers) → the exactly-once ledger is the source of truth;
  the counter is derived, so concurrent settles can't oversend past the unique key.
- **Clock/timezone** → windows are tenant-timezone days; quiet-hours respected.

---

## 6. Verification (eng test plan)

1. **Suppression-first**: a listed email (each reason) → `SKIP_SUPPRESSED`, no
   verify, no send. Domain block suppresses sub-addresses. Re-ingest idempotent.
2. **Ingestion**: an unsubscribe/bounce/complaint event → a suppression row +
   (bounce/complaint) a sequence hard-stop. Re-delivered event → no dup.
3. **Deliverability**: undeliverable blocked; risky escalates; `mx_check=False`
   downgrades; verdict cached + re-verified after TTL/bounce.
4. **Cap/throttle**: at-cap inbox defers the candidate (not drop); counter reads
   off settled sends only (a retry doesn't double-count); multi-inbox rotation
   stays within per-inbox caps.
5. **Threshold auto-revert**: synthetic bounce/complaint rate breach → inbox
   reverts to MANUAL + alert.
6. **439**: with the hold on, no candidate auto-sends regardless of gates passing.

---

## 7. Build order (suggested)

1. `suppression_entry` table + `SuppressionStore` (+ RLS) + seed loader.
2. Ingestion handlers (unsub endpoint, bounce/complaint via `capture.py` seam).
3. `deliverability_verdict` cache + live `mx_check` seam (rate-limited).
4. `outreach_send_counter` + throttle gate + scheduler deferral + rotation.
5. Threshold monitor + auto-revert wire into the per-channel autonomy dial (439).
6. Swap `OutreachPolicy` to the DB-backed gates; keep the in-memory ones for tests.

All five are independent of the per-touch **copy** (writer, 1mk.5 email mode) and
the live **send connector** (eng, Gmail) — those compose at the boundary, 439-gated.
