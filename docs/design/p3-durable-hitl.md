# P3 — Durable Long-Horizon HITL: wiring design

**Status:** foundation landed (`engine/studio/durable_run.py` + `engine/tests/test_durable_run.py`); wiring below is a plan, NOT yet applied.
**Scope:** how the standalone durable-run primitive gets wired into the live campaign run so a run can pause mid-way, ask the operator, survive a process restart, and resume — exactly-once preserved, HELD/approve-first intact.
**Owner of the run loop:** another agent owns `engine/studio/agui.py`; this doc only *designs* the wiring there. The primitive itself is complete and tested.

---

## 1. Problem

Today a campaign run is fire-and-forget and its progress lives only in memory:

- `launch_studio_run` (`engine/studio/agui.py:1550`) mints `run_id = f"team-{campaign_id}-{…}"`, records status in the **in-memory** dict `app.state._studio_runs` (`agui.py:1548,1552,1564`), and runs the body as a background `asyncio.to_thread(_execute_campaign_sync, …)` (`agui.py:1561`).
- The body `_execute_provided_leads_sync` (`agui.py:1101`) is an **imperative per-lead `for` loop** (`agui.py:1225`): research → analyst → offer → draft → critic → stage a PENDING/HELD action → memory write, per lead, then a final jury summary + `runs` row.

If the process restarts mid-run, the in-memory `_studio_runs` entry is gone, there is no pause/ask/resume point, and re-running the body from scratch would re-do per-lead work. We want the "eureka" mid-run pause: stop at an exact point, persist everything, let the operator weigh in (e.g. approve the campaign angle before the whole cohort is drafted), then resume — **without re-firing anything already done**.

The **exactly-once anchor already exists**: each staged draft is written with `record_pending_action(idempotency_key=f"{run_id}:{cust_id}")` (`agui.py:1381`), and that key is `UNIQUE … ON CONFLICT DO NOTHING` (`engine/actions/store.py:147`). So re-processing a lead is already de-duplicated at the DB. The durable primitive adds the *pause/resume + full-state checkpoint* around it and generalizes the exactly-once guard to every side-effecting unit, not just the action row.

---

## 2. Chosen approach (and why not the others)

The engine already runs a **LangGraph** `AsyncPostgresSaver` with real `interrupt()` / `Command(resume=…)` + an fk5 replay guard — but only for the *graph* spine (`engine/harness/graph.py:70,138`). The campaign body is not a graph; it is a straight-line loop. Three real 2026 options were evaluated:

| Option | Fit here | Verdict |
|---|---|---|
| **LangGraph** `interrupt`/`Command` + `PostgresSaver` ([docs](https://docs.langchain.com/oss/python/langgraph/interrupts)) | Would require rewriting `_execute_provided_leads_sync` as a `StateGraph` with a loop-back edge + counter, and pulling the optional `langgraph-checkpoint-postgres` extra into the default/test venv (it is `[postgres]`-only, `engine/pyproject.toml:49`). | **Too heavy for the imperative loop.** Semantics adopted, engine not. |
| **Temporal** durable workflow + signal-based approval, deterministic replay, idempotent activities ([Temporal HITL](https://docs.temporal.io/ai-cookbook/human-in-the-loop-python)) | New infra (server + workers) for one pause point. | Overkill for the foundation; noted as the scale path. |
| **Google ADK** `LongRunningFunctionTool`, checkpoint-after-every-tool, durable session ([ADK blog](https://developers.googleblog.com/build-long-running-ai-agents-that-pause-resume-and-never-lose-context-with-adk/)) | Different agent runtime than ours. | Not our stack. |

**Decision:** implement the *same semantics* these converge on, directly on `psycopg` (a **core** dependency — no new install, runs under the standard `--extra observability` test venv), as `engine/studio/durable_run.py`. This is our own vetted logic, and it sits behind the same swap seam `engine/harness/runstore.py:10` already documents ("the canonical durable substrate is the LangGraph Postgres checkpointer, but DBOS … is kept slot-able behind this protocol"). If the loop is ever converted to a graph, the `interrupt`/`resume`/`step` surface maps 1:1 onto LangGraph and can be swapped underneath callers.

The design borrows the **exactly-once mechanic verbatim** from the two 2026 patterns that matter:

- LangGraph's documented resume caveat — *"when the graph resumes, the node restarts from the beginning; all code before `interrupt()` re-runs"* — means resume is a **replay**. We embrace that: `resume()` re-drives the body from the top.
- Temporal makes replay safe with **idempotent activities**; ADK **checkpoints after every step**. We do both: every side-effect goes through `step(key, fn)`, which claims `(run_id, step_key)` in `durable_step_ledger` (`ON CONFLICT DO NOTHING` — the HARN-04 boundary pattern, `engine/sideeffects/boundary.py:45`) and commits the effect *in the same transaction*. On replay a claimed step returns its recorded result and does **not** run `fn` again.

---

## 3. The primitive (what landed)

`engine/studio/durable_run.py` — `DurableRun(run_id, tenant_id, dsn)`:

- **`interrupt(payload) -> answer`** — pause point. First reach: persist the full checkpoint (`status='interrupted'`, `cursor`, `state`, and the `payload` question) and raise `DurableInterrupt`. On a later replay it returns the operator's answer instead of raising.
- **`resume(Command(resume=value), fn) -> RunOutcome`** — record the answer durably against the pending interrupt ordinal, flip to `running`, and re-drive `fn` from the top.
- **`step(step_key, fn) -> result`** — exactly-once wrapper. Claim `(run_id, step_key)`, run `fn(conn)` on the *same* transaction, commit effect+ledger atomically; a re-reached step is a no-op returning the recorded result.
- **`run(fn)`** — start fresh; rejects a completed `run_id` (`RunAlreadyCompletedError`, fk5 analogue), routes a paused run to `resume()`, and re-drives a crashed (`running`) run as recovery.
- **`DurableRun.load(run_id, dsn)`** — rehydrate a *fresh object over a fresh connection* from `durable_run_checkpoint`. This is the restart path.

Two idempotent tables (inline DDL, `CREATE … IF NOT EXISTS`, like `runstore.py`): `durable_run_checkpoint` (one row per run: full-state snapshot + pause marker + durable resumes map) and `durable_step_ledger` (`UNIQUE(run_id, step_key)`).

---

## 4. Where the interrupt points go in `_execute_provided_leads_sync`

The wiring is **additive** and gated — behind a flag so the current synchronous behavior is unchanged until turned on. Sketch (pseudo-diff against `agui.py:1101`):

```python
def _execute_provided_leads_sync(plan, session_id, tenant_id, dsn, run_id=None):
    ...
    run = DurableRun(run_id, tenant_id, dsn=dsn); run.ensure_schema()

    def body(run):
        # (A) STRATEGIST once (agui.py:1198). Wrap so the angle is computed once.
        strategy = run.step(f"{run_id}:strategist", lambda conn: _run_strategist(...))
        campaign_angle = strategy.get("target_angle")

        # (B) EUREKA PAUSE — the mid-run ask. Stop BEFORE drafting the whole cohort
        #     and let the operator approve/adjust the angle. Skippable via plan flag.
        if plan.pause_for_angle_approval:
            decision = run.interrupt({
                "kind": "approve_angle",
                "angle": campaign_angle, "n_leads": len(leads),
            })
            if decision.get("action") == "reject":
                campaign_angle = decision.get("angle") or campaign_angle   # operator edit
            # 'approve' → fall through and draft the cohort with the (possibly edited) angle

        # (C) PER-LEAD loop (agui.py:1225). Each lead is ONE durable step keyed by
        #     the SAME idempotency handle the action row already uses.
        for facts in leads:
            cust_id = facts["customer_id"]
            def stage_lead(conn, facts=facts):
                # research → analyst → offer → draft → critic → record_pending_action
                # (agui.py:1227-1395), UNCHANGED, but the record_pending_action call
                # rides `conn` so it commits atomically with the ledger claim.
                return _stage_one_provided_lead(conn, facts, ...)
            run.step(f"{run_id}:{cust_id}:stage", stage_lead)
            run.checkpoint(cursor=run.cursor + 1)   # advance progress marker

        # (D) Optional per-batch pause every N leads for very large cohorts:
        #     `if i and i % batch == 0: run.interrupt({"kind":"batch_checkpoint", ...})`

        # (E) Final jury summary + runs row (agui.py:1398-1410) — also a step.
        return run.step(f"{run_id}:jury", lambda conn: _finalize(...))

    outcome = run.run(body)          # or run.resume(Command(resume=answer), body) on resume
    return _summary_from(outcome)
```

Interrupt points, concretely:

1. **Eureka angle-approval pause (primary)** — after the strategist (`agui.py:1198`), before the per-lead loop (`agui.py:1225`). This is the highest-value pause: the operator approves the *strategy* before the engine spends N drafts on it, and can edit the angle in-flight.
2. **Per-batch checkpoint pause (optional)** — every N leads inside the loop, for long cohorts, so the operator can stop a run that is drifting.
3. **The existing HELD gate is unchanged** — nothing sends. Each lead still stages a PENDING action (`agui.py:1375-1383`); the durable interrupt pauses the *run*, the existing approve-first still gates each *send*. The reject path in the test (`test_reject_answer_holds_remaining_sends`) proves an operator "reject" answer flows through the interrupt and holds the remaining work.

---

## 5. How resume replays without re-firing (exactly-once preserved)

On resume the body re-drives from the top. Each unit is protected two ways (belt-and-suspenders):

1. **`durable_step_ledger`** — `run.step(f"{run_id}:{cust_id}:stage", …)` is a ledger no-op for every lead already staged, so the whole research→draft→critic chain for a completed lead is skipped (no re-spend, no re-fire).
2. **`actions.idempotency_key`** — even if a step were re-run, `record_pending_action(idempotency_key=f"{run_id}:{cust_id}")` (`agui.py:1381`, `actions/store.py:147`) still returns the existing action id rather than inserting a duplicate.

Because `stage_lead` runs `record_pending_action` on the step's transaction, the action row and the ledger claim **commit together**. The proof test uses a *deliberately non-idempotent* insert (no unique key) to show the ledger alone stops the double-fire — the real wiring is even safer because the action key is also unique.

**True external sends** (a real email/post — not the HELD staging) must NOT use the in-transaction `step()`; they go through the existing **two-phase outbox** (`engine/sideeffects/boundary.py` + `dispatcher.py`: durable `SENDING` claim → connector → `SENT`, `engine/tests/test_exactly_once.py`), which is at-least-once delivery + an idempotent connector. The durable run *enqueues intent* in a `step()` (exactly-once intent), the dispatcher *delivers* (exactly-once effect via the keyed connector). This separation is called out so no one wraps a live send in the in-tx form and assumes a network send rolls back.

---

## 6. Crash-window enumeration

Enumerated explicitly because I have double-fired once before by not doing this ([[exactly-once-crash-window-rigor]]). Each window is covered by a test.

| # | Crash point | Recovery behavior | Safe? | Test |
|---|---|---|---|---|
| 1 | Before any step commits | `run()` re-drives; nothing was done | ✅ | `test_restart_resume_exactly_once_no_refire` |
| 2 | Inside `step()` after `fn` INSERT, before commit | Whole tx rolls back — no effect, no claim; retried cleanly | ✅ exactly-once | `test_step_is_atomic_on_failure` |
| 3 | After `step()` commits, **before** `checkpoint()` advances cursor | Ledger claim present; replay re-reaches the step → no-op, no re-fire (cursor is an observability marker, the ledger is the authority) | ✅ | `test_crash_between_step_commit_and_checkpoint_no_refire` |
| 4 | At the interrupt (paused) | Checkpoint persisted `interrupted`; `load()` rehydrates; `resume()` continues | ✅ | `test_restart_resume_exactly_once_no_refire`, `test_interrupt_persists_full_state_to_postgres` |
| 5 | During `resume()`, after the answer is persisted, before the drive finishes | Answer is durable in `resumes`; a second `load()`+`resume` replays past the (now-answered) interrupt, completed steps skipped | ✅ | covered by resume durability + `test_two_interrupts_pause_twice_then_complete` |
| 6 | Re-running a completed run / double-resume | Rejected (`RunAlreadyCompletedError` / `DurableResumeError`) — the fk5 replay guard, so append-only surfaces don't re-accumulate | ✅ | `test_run_of_completed_run_is_rejected`, `test_double_resume_is_rejected` |

The one honest residual: window 3 is *at-most-once for the cursor*, exactly-once for the *effect*. If a crash lands between the effect commit and the cursor bump, the cursor under-reports progress by one, but the effect never repeats. That is the correct trade — never double-fire, at worst re-observe. A true external send is handled by the outbox (§5), not this window.

---

## 7. Voice UI reconnect / resumable SSE

Today the run's live status is the **in-memory** `app.state._studio_runs[run_id]` dict (`agui.py:1548`), and the voice path launches via `POST /studio/voice/orchestrate` → `launch_studio_run` (`voice.py:462`, `agui.py:1550`), tying `run_id` to the voice `sessionId`. That dict does not survive a restart, and an SSE/voice client that reloads the tab loses the stream.

The durable checkpoint makes the **run itself the session of record**, so reconnect becomes replay-then-tail:

1. **Durable session key.** `run_id` (already `sessionId`-derived) keys `durable_run_checkpoint`. On reconnect the client sends `run_id`; the server reads the row — `status` (`running`/`interrupted`/`completed`), `state` (progress), and `interrupt` (the pending question, if paused) — and reconstructs where the operator is, even after a server restart. No more reliance on `_studio_runs`.
2. **Replay backlog, then live tail.** The SSE endpoint first replays the persisted trajectory — the per-node spans in `runs.steps[]` (`engine/harness/runstore.py:238`, the append-only JSONB the console already reads) plus the `durable_run_checkpoint.state` snapshot — then tails new frames, exactly as `CompiledGraph.astream` relays per-node updates (`harness/graph.py:146`). Tab-switch/reload/restart re-fetches the backlog and continues.
3. **`Last-Event-ID` cursor.** Emit each frame with an SSE `id:` equal to the durable `cursor` (or the span `seq`). On reconnect the browser's `EventSource` sends `Last-Event-ID`; the server replays only frames after it, so the stream resumes with no gap and no duplication — the SSE analogue of the step ledger.
4. **Paused-run UX.** When `status='interrupted'`, the reconnecting client renders the `interrupt.payload` (the "approve the angle?" ask) and, on the operator's answer, calls a resume endpoint that invokes `DurableRun.load(run_id).resume(Command(resume=answer), body)`. The voice agent's GO-gate (`voice.py:442`) is unchanged; this adds a *resume*-gate with the same server-side-authoritative pattern.

None of this is wired yet; it is the target for the P3 voice-reconnect slice that builds on this foundation.

---

## 8. Rollout / ownership

- **Additive + flagged.** The wiring adds a `DurableRun` wrapper and interrupt points behind a `plan.pause_for_angle_approval`-style flag; default off preserves today's straight-through run. No frozen file (`eng5/src`, `cells/*`, `psych_profile.py`) is touched, and `agui.py` changes are owned by the run-loop agent.
- **Schema.** `durable_run_checkpoint` + `durable_step_ledger` self-create via `ensure_schema()` (idempotent). Promote to an `infra/initdb` migration when wired, alongside the existing `runs` / `actions` / `outbox` DDL.
- **Swap seam.** Callers depend only on `interrupt`/`resume`/`step`; the psycopg substrate can later be swapped for LangGraph `PostgresSaver` or DBOS behind `runstore.py`'s protocol without touching the loop.

## 9. References

- LangGraph interrupts & `Command(resume)`, PostgresSaver durability + the "node re-runs from the start on resume" caveat — https://docs.langchain.com/oss/python/langgraph/interrupts
- Temporal durable execution: signal-based HITL waits, deterministic replay, idempotent activities/signal handlers — https://docs.temporal.io/ai-cookbook/human-in-the-loop-python
- Google ADK long-running agents: checkpoint-after-every-tool, durable session, `LongRunningFunctionTool` pause/resume — https://developers.googleblog.com/build-long-running-ai-agents-that-pause-resume-and-never-lose-context-with-adk/
- In-repo prior art: `engine/harness/graph.py` (LangGraph interrupt/resume + fk5 guard), `engine/harness/runstore.py` (DBOS-swappable durable-run protocol), `engine/sideeffects/boundary.py` + `engine/tests/test_exactly_once.py` (HARN-04 two-phase exactly-once for external sends).
