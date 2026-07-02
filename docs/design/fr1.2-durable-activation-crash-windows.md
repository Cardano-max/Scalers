# fr1.2 (OPS-2) — durable-run activation: crash-window enumeration

> Team exactly-once rule: enumerate **every** crash window + adversarial review
> before claiming done. This is the artifact for bead **CustomerAcq-fr1.2**.
> Builds on the design in `p3-durable-hitl.md` §5–§6 (as corrected by 2gh); this
> file pins the windows to the **actual wiring** that landed, not the plan.

## What was wired

Two independent durability layers, activated by `ENGINE_DATABASE_URL`:

- **Graph spine (AC2):** `archetypes.compose.run_campaign` compiles the campaign
  graph with the sync `PostgresSaver` and invokes through `_run_guarded` — the
  fk5 completed-thread replay guard (`compose.py`). Covered by
  `tests/test_compose_durable.py`.
- **Per-lead body (AC3):** `studio.agui._execute_provided_leads_sync` wraps each
  lead with the `studio.durable_run` step ledger — a **replay-skip marker** at
  the loop top + a **step record** at the loop bottom.

Three exactly-once authorities, one per concern (the 2gh layering — unchanged):

1. **Staged action row exists once** → `actions.idempotency_key = "{run_id}:{cust_id}"`,
   `UNIQUE … ON CONFLICT DO NOTHING`, written on `record_pending_action`'s own
   autocommit connection. **This is the authoritative effect guard.**
2. **Per-lead orchestration not re-run on replay** → `durable_step_ledger`,
   key `"{run_id}:{cust_id}:stage"`, `UNIQUE(run_id, step_key)`. A **cost guard**
   (skip the expensive re-draft), not the effect guard.
3. **External send delivered once** → HARN-04 outbox + `side_effect_ledger`.
   Out of scope here: this path only stages HELD/approve-first actions; nothing
   sends.

## Per-lead timeline (lead `C`, ledger key `K = "{run_id}:{C}:stage"`)

```
 T0  loop top: has_run_step(K)?              [read-only]         -> if claimed: re-attach prior action_id, CONTINUE (skip)
 T1  research → analyst → offers → draft →   [LLM/in-memory]      (agent_runs rows via _rec are append-only telemetry)
     dossier → critic
 T2  record_pending_action(idem="{run_id}:{C}", dsn)  [COMMIT: staged action row]   <-- SIDE EFFECT (HELD row)
 T3  pending.append(action_id)               [in-memory]
 T4  store.write(memory)                     [best-effort, own conn]
 T5  _durable.step(K, fn)                     [COMMIT: ledger claim+result]           <-- COST-GUARD marker
 T6  _durable.checkpoint(cursor=len(pending)) [COMMIT: checkpoint row]
 T7  next lead
```

`fn` at T5 writes **nothing** on the step's `conn` (the row was already committed
at T2 on its own connection), so `step()` here is purely the replay-skip marker
that `p3-durable-hitl.md §5.1` prescribes.

## Crash windows

| # | Crash point | On restart (same `run_id`) | Skip? | Double-draft? | Double side-effect? |
|---|-------------|-----------------------------|-------|---------------|---------------------|
| W1 | during T1 (before T2) | `has_run_step(K)` False → re-process `C` from scratch | no | no (first draft) | no (no row yet) |
| W2 | between T2 and T5 (**row committed, ledger not**) | `has_run_step(K)` False → re-process → `record_pending_action` hits `ON CONFLICT DO NOTHING` → returns the **existing** action_id → ledger now records | no | **re-draft (wasted LLM work)** | **no** — idem key `{run_id}:{C}` blocks a 2nd row |
| W3 | between T5 and T6, or between leads | `has_run_step(K)` True → skip re-draft, re-attach prior action_id | no | no | no |
| W4 | mid-`record_pending_action` commit | psycopg commit is atomic → either W1 (no row) or W2 (row). No partial row. | no | maybe (W2) | no |
| W5 | mid-`step()` commit | atomic claim+result commit → either not-claimed (re-run, = W2) or claimed (skip, = W3) | no | maybe (W2) | no |
| W6 | anywhere, then restart re-invokes the whole function | loop re-iterates **all** leads; completed skip (ledger), the crashed lead re-processes, the rest process | no | no (completed skip) | no (idem key) |
| W7 | two concurrent drives, same `run_id` | ledger `UNIQUE(run_id,step_key)` serializes the marker; `idempotency_key` `UNIQUE` serializes the row | no | duplicated *work* possible | **no** (both UNIQUE guards hold) |

**The dangerous window — "side-effect durable but exactly-once guard not" — cannot
occur for a *double side effect*:** the action row's `idempotency_key` is written
in the same atomic `INSERT … ON CONFLICT` that decides existence, independent of
the ledger. The ledger lagging behind the row (W2) only costs a re-draft, never a
second row. This is why effect exactly-once is delegated to the idem key, not the
ledger (2gh correction).

## Honest residuals (efficiency / UX only — not correctness, not blockers)

- **W2 wasted re-draft:** a crash after staging but before the ledger record makes
  the restart re-run that one lead's LLM cells. No double effect; just cost.
- **W3 cursor under-report:** `checkpoint(cursor)` at T6 can lag the true progress
  by one lead if the crash lands between T5 and T6. Observability only.
- **W7 concurrent duplicated work:** two drives of the same `run_id` each do the
  work; both UNIQUE guards prevent double *effects*. Optional hardening —
  `pg_try_advisory_lock(hashtext(run_id))` around the drive — is a follow-up, not
  required for exactly-once (filed as a note, mirrors `p3-durable-hitl.md §6`).

## Deactivated path (no `ENGINE_DATABASE_URL`)

`_durable` stays `None` and the loop is byte-for-byte the pre-fr1.2 behavior; the
compose graph uses `InMemorySaver`. Durability is opt-in via the one env flag, so
the test suite and the in-memory demo are unchanged.

## Adversarial review

Enumeration reviewed against the three failure modes the AC names — *lead skipped*,
*lead double-drafted*, *side effect re-fired* — across all seven windows: none can
produce a skipped lead or a re-fired side effect; double-*draft* (wasted work,
never a second staged row) is possible only in W2/W7 and is an accepted efficiency
residual. The proof is exercised end-to-end in `tests/test_provided_leads_durable_crash.py`
(crash between leads **and** mid-lead between the staged row and the ledger record).
