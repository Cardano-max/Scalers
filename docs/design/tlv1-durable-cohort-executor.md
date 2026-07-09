# tlv.1 (IMPACT r1) — durable 500-cohort executor

> Team exactly-once rule: enumerate **every** crash window + adversarial review
> before claiming done. This is the artifact for bead **CustomerAcq-tlv.1**.
> Builds directly on `fr1.2-durable-activation-crash-windows.md` (the per-lead
> W1–W7 model, unchanged) and adds the **wave** + **cohort-freeze** + **startup
> re-drive** layers on top.

## The ceiling we are breaking

`studio.agui._execute_provided_leads_sync` is a single sequential per-lead
`for` loop bounded by
`effective_cap = min(blueprint.stop_conditions.total_quota or _OUTPUT_HARD_CAP, _OUTPUT_HARD_CAP)`
with `_OUTPUT_HARD_CAP = 12` (`archetypes/compose.py:260`). A 500-row CSV yields
`total_quota=500` but `effective_cap=12`: rows 13..500 are skip-ledgered
`"beyond output cap of 12"` and **never processed**. There is no wave, no cohort
cursor, and no re-drive entry point (the durable machinery is reachable only by
re-invoking the executor with the same `run_id`, which nothing does after a
crash — `launch_studio_run` always mints a fresh `run_id`).

## Cap decoupling — the one real design judgment (NOTE the bead wording)

The bead says "remove/raise `_OUTPUT_HARD_CAP` … + the effective_cap **floor**
(agui.py:1341-1343)". Two corrections from the code:

1. agui.py:1341-1343 is a `min()` **ceiling**, not a floor (`or` turns
   `total_quota==0` into 12). There is no floor there.
2. `_OUTPUT_HARD_CAP` is **shared** by two pipelines: (a) the compose spine's
   `_planned_channels`, where it caps a **parallel** LangGraph `Send` fan-out —
   every draft worker runs in ONE superstep, so raising it directly multiplies
   *concurrent* LLM calls (the genuine anti-runaway hazard the constant exists
   for); and (b) this **sequential** provided-leads loop, one lead at a time.

Raising the single shared constant to 500 would let an absurd interview answer
("make 5000 emails") spawn 5000 concurrent model calls on the compose path.
**Decision:** keep `_OUTPUT_HARD_CAP = 12` for compose (parallel fan-out stays
bounded — invariant preserved), and give the sequential cohort executor its own,
larger, wave-checkpointed ceiling `_COHORT_HARD_CAP` (default 1000, override via
`ENGINE_COHORT_HARD_CAP`). The executor stops using compose's constant entirely.
A 500-row CSV then reconciles all 500 rows (0 "beyond output cap"); a 5000-row
CSV still stops at `_COHORT_HARD_CAP` with an honest skip reason — the
bounded-fan-out safety invariant holds on BOTH paths.

## Wave model

The frozen cohort is processed in bounded, resumable **waves** of
`_WAVE_SIZE = 25` leads. `pending` accrues across waves; a `checkpoint(cursor=N)`
persists after each wave boundary, where `cursor` = number of cohort entries
**consumed** (attempted — staged OR skipped), i.e. the ordinal into the frozen
cohort list. Waves exist to (a) bound the durable-checkpoint granularity for a
long run and (b) give the UI/board real cross-wave progress. Per-lead
exactly-once is UNCHANGED — the `durable_step_ledger` key `"{run_id}:{cust_id}:stage"`
and the `actions.idempotency_key "{run_id}:{cust_id}"` remain the authorities.

## Cohort freeze (deterministic enumeration)

Cohort selection (`churn_risk_leads` `ORDER BY created_at`, `conversation_leads`
`ORDER BY MIN(created_at)`) has **no unique tiebreaker** and re-evaluates
membership on every call — a persona edit or a new import between waves would
shift the page. So the full ordered `customer_id` list is **frozen once at wave 0**
into `durable_run_checkpoint.state["cohort_ids"]` (the provided-CSV path is
already frozen — ids arrive in the plan). Every resume reads the frozen list;
the cohort is never re-enumerated. `state` also carries `plan` (model_dump),
`session_id`, and `executor: "cohort"` so a cold-start supervisor can rebuild the
`CampaignPlan` and re-drive with zero external input.

## Terminal status + eager row (two fr1.2 bugs this fixes)

The live loop today (a) creates the checkpoint row **lazily** on the first
`checkpoint()` after lead 1 — a crash before lead 1 leaves NO row for a scanner
to find; and (b) **never** advances status past `'running'` — every finished run
looks stranded forever. Both break a startup re-driver. Fixes:

- Persist `status='running'` + the frozen cohort/plan **at loop start** (cursor 0),
  before any lead — the row always exists.
- At the end, `_durable.finish(run_status)` writes the terminal
  `'completed'`/`'failed'` (new public method on `DurableRun`, thin wrapper over
  the existing `_persist`). A re-drive scanner keys on `status='running'` and so
  never touches a finished run (and `DurableRun.run()`'s existing
  `RunAlreadyCompletedError` guard reinforces it).

## Startup re-drive supervisor

`main.py` gains its first FastAPI `lifespan` (gated on `get_settings().database_url`
and `settings.cohort_supervisor` — env `ENGINE_COHORT_SUPERVISOR`, default on).
On startup it fire-and-forgets a task `redrive_stranded_cohort_runs(dsn)` that:

1. `ensure_schema()` (durable tables exist before any request path).
2. Selects `durable_run_checkpoint WHERE status='running' AND state->>'executor'='cohort'`.
3. For each, under a **single-flight** `pg_try_advisory_lock(hashtext(run_id))`
   (mandatory now a supervisor exists — the fr1.2 W7 hardening), rebuilds the
   `CampaignPlan` from `state['plan']` and re-invokes `_execute_provided_leads_sync`
   with the ORIGINAL `run_id` in `asyncio.to_thread`. The per-lead ledger
   replay-skips everything already staged; the run continues at the exact next
   unstaged lead and finishes, marking a terminal status.

Import-time app creation stays side-effect-free (lifespan runs only when the
server actually serves; bare `TestClient(app)` does not trigger it). The scan
touches only `executor='cohort'` rows, so unrelated stranded runs are ignored.

## Crash windows — wave/cohort layer (per-lead W1–W7 inherited unchanged)

Timeline additions (`WB` = wave boundary checkpoint at cursor=N):

```
 S0  loop start: persist status='running' + freeze {plan, cohort_ids} (cursor 0)  [COMMIT]
 ... per-lead T0–T7 exactly as fr1.2, for leads [0..24]
 WB0 checkpoint(cursor=25)                                                          [COMMIT]
 ... leads [25..49] ...
 WBk checkpoint(cursor=(k+1)*25)
 ...
 F   finish(run_status='completed'|'failed')                                        [COMMIT]
```

| # | Crash point | On restart (same `run_id`, or supervisor re-drive) | Lead skipped? | Double-stage? |
|---|-------------|-----------------------------------------------------|---------------|----------------|
| C1 | before S0 (no row) | nothing to re-drive; a manual re-run with same run_id enumerates+freezes fresh; ledger empty → clean first run | no | no |
| C2 | after S0, before lead 0 stages | supervisor finds status='running'+cohort; re-drive from frozen cohort; ledger empty → all leads processed once | no | no |
| C3 | mid-wave k (some leads staged+ledgered, some not) | re-drive: frozen cohort re-iterated; ledgered leads replay-skip (no re-draft, no 2nd row via idem key), unstaged leads process; **inherits fr1.2 W2/W3 residual** (a lead crashed between its action-row commit and its ledger commit re-drafts once, never double-rows) | no | no |
| C4 | at a wave boundary (between WBk commit and next lead) | cursor=N durable; re-drive fast-forwards consumed leads via ledger, continues at N | no | no |
| C5 | after F (`completed`) then re-invoked | status='completed' → supervisor skips it; a manual `run()` raises `RunAlreadyCompletedError`; a raw re-invoke of the loop finds every lead ledgered → full no-op (0 new drafts, 0 new rows) — the existing `test_completed_run_replay_is_full_noop` guarantee, now at cohort scale | no | no |
| C6 | two drives of same run_id (supervisor races a live drive, or two workers) | `pg_try_advisory_lock(hashtext(run_id))` grants one; the loser no-ops. Even without the lock, `UNIQUE(run_id,step_key)` + `idempotency_key UNIQUE` bound EFFECTS to once (duplicated *work* is the only residual) | no | no |

**Honest residuals (efficiency/UX only, not correctness):** C3 re-drafts at most
the leads of the crashed wave that had staged their row but not their ledger
marker (fr1.2 W2, ≤ wave size); cursor lags true progress by up to one wave
(observability only — the ledger is truth). A skipped lead (no email, etc.) with
no ledger row is re-evaluated on a mid-wave resume; the skip is deterministic and
pre-LLM, so it costs no model call.

## Reconciliation across waves

`expected` = the full frozen cohort size (500). Every entry ends as a staged
draft OR a `skipped` row with a concrete reason (`no email`, `not found`,
`beyond cohort cap of 1000`). `reconciled = drafted + skipped >= expected`. Zero
`"beyond output cap"` for any cohort ≤ `_COHORT_HARD_CAP`.

## Files

- `studio/durable_run.py` — add `finish(status, result=None)` + module helper
  `list_running_cohort_runs(dsn)`; promote DDL to `infra/initdb/19-durable-run.sql`.
- `studio/agui.py` `_execute_provided_leads_sync` — cap decoupling, cohort freeze,
  wave loop, eager `running` row, terminal `finish`.
- `studio/cohort_supervisor.py` (new) — `redrive_stranded_cohort_runs(dsn)` with
  advisory-lock single-flight.
- `main.py` — gated `lifespan` launching the supervisor.
- `infra/initdb/19-durable-run.sql` (new) — durable-run tables (idempotent).
- Tests: `test_cohort_executor_waves.py`, `test_cohort_supervisor.py` (new),
  plus the existing `test_provided_leads_durable_crash.py` stays green.
