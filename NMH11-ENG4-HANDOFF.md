# nmh.11 — eng4 recon handoff to eng3

eng4 stood down (eng3 owns nmh.11). This is my independent recon (4 parallel Opus agents)
+ the shared helper, as **support** for eng3's fix. It is recon + a runnable recipe, NOT a
written test/fix — eng4 did not touch the staging code.

## Root cause — CONFIRMS eng3's 10:50 diagnosis exactly
1. **Fresh `run_id` per retry:** `launch_studio_run` (`engine/studio/agui.py:2307-2308`) mints a
   new `run_id` every call; the in-memory `runs_registry` (`agui.py:2305,2309`) is non-durable.
2. Idempotency key `{run_id}:{cust_id}` (`agui.py:1988`) + `ON CONFLICT DO NOTHING` → a new
   run_id re-stages the SAME customer as a **phantom duplicate**; customers not re-picked
   **orphan under the dead run_id** and drop from the run-scoped view = "vanish".
3. **Not all-or-nothing:** N independent autocommits (`actions/store.py:98-102,139`), no txn
   around the batch (`agui.py:1731` loop, `compose.py:494-516` loop).
4. **Cap blocker:** `_OUTPUT_HARD_CAP=12` (`archetypes/compose.py:260`) applied at
   `agui.py:1495-1497`; loop breaks at `len(pending) >= effective_cap` (`agui.py:1732`) →
   N=25/30 clip to 12 (proven by `tests/test_draft_count_exactness.py:131-147`).
5. Team/compose path is worse than provided path: `{run_id}:{random asset_id}` where
   `asset_id` is a fresh uuid (`compose.py:381,511`) — not stable even on same-run_id resume.
   The **provided-leads path** (`_execute_provided_leads_sync`, `agui.py:1417`) is the good
   one: stable `{run_id}:{cust_id}` + DurableRun replay-skip (`agui.py:1714-1745,2010-2020`).

## Angel-1200 N=10/25/30 real-run recipe (for the AC proof)
"Angel 1200" = a BRIEF (artist="Angel", offer_price_usd=1200), NOT a tenant. Use the
**provided-leads path** (per-recipient real `target=email`); the broadcast
`campaign_generator` sets `target=None` (that's why tlv.6 had null targets) — do NOT use it.

Prereq: seed >= N contactable customers (distinct email + `email_opt_in=True`) for the tenant
(model: `tests/test_draft_count_exactness.py:46-66`). Then per N:
```python
from studio.agui import CampaignPlan, _execute_provided_leads_sync
plan = CampaignPlan(lead_source="provided",
    goal="Angel full-day special — $1,200 (win back lapsed clients)",  # price in goal only (offer anti-fab)
    channels=["gmail"], output_count=N, lead_count=N,
    customers={"rows": N, "columns": ["name","email"]})
summary = _execute_provided_leads_sync(plan, "sess_angel1200", TENANT, DSN, None)
run_id = summary["run_id"]  # team-camp_...  NOT demo-  (08-actions.sql:57 flips demo-% is_seeded=true)
```
Env: `SCALERS_OUTREACH_LLM=0`, `SCALERS_EMBEDDER=deterministic`, `ENGINE_DATABASE_URL=...`.
Gotchas that silently drop below N (would fail exact-N): offer anti-fab skip (`agui.py:1855`),
consent/no-contact skip (`agui.py:1834`, `entities.py:146`). N=25/30 REQUIRE the cap decouple.

## DB proof (per run_id — the AC proof)
```sql
SELECT count(*) AS n, count(DISTINCT target) AS n_distinct,
       bool_and(target IS NOT NULL AND target<>'') AS targets_real,
       bool_and(is_seeded=false) AS all_live, bool_and(channel='gmail') AS all_gmail
FROM actions WHERE run_id = :run_id;   -- PASS: n=N, n_distinct=N, all t
```
Retry-stability: capture `array_agg(id ORDER BY id)` before + after a same-`run_id` retry —
must be identical (compare row **ids**, not just counts). Research-ran evidence:
`SELECT role,count(*) FROM agent_runs WHERE run_id=:run_id GROUP BY role;`.

## Test gap
No existing test covers retry-stability on the provided-leads **actions** path
(`test_sms_staging_pg.py` is the SMS outbox path, different). A new regression test must
**fail-first** on the unpatched code (stage N, retry with a NEW run_id, assert the queue did
NOT grow / ids stable).

## Shared helper (eng2 contract)
`engine/studio/customer_dal.py` — `customer_exists(tenant,customer_id,dsn)`: tenant-scoped
`SELECT 1 FROM customers WHERE tenant_id=%s AND id=%s LIMIT 1`, **no try/except** (DB error
propagates). eng2's verify-before-write pattern, extracted per their contract. eng3's plan
uses a DB partial-unique guard instead, so this may be unused for nmh.11 — kept for tlv.2.
