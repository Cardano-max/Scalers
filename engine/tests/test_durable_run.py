"""Durable long-horizon HITL foundation tests (P3): the interrupt -> persist ->
**real process restart** -> resume proof, with exactly-once preserved.

The headline guarantee (operator demand): a run pauses at an exact point,
persists its full state to REAL Postgres, and — after a *fresh object over a
fresh connection*, standing in for a restarted process — resumes and finishes
WITHOUT re-firing any side-effect that already completed. The side-effect under
test is DELIBERATELY non-idempotent (a plain INSERT, no unique key), so only the
durable ``step()`` ledger can stop a double-fire — proving the primitive, not a
lucky idempotent effect.

All tests run against the real local Postgres (UNIQUE constraints + atomic
commit genuinely exercised), so they carry the ``integration`` marker like the
HARN-04 exactly-once suite. Start it with: cd infra && docker compose up -d
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from psycopg.rows import dict_row

from studio.durable_run import (
    Command,
    DurableInterrupt,
    DurableResumeError,
    DurableRun,
    RunAlreadyCompletedError,
    RunNotFoundError,
    default_dsn,
    ensure_schema,
)

pytestmark = pytest.mark.integration

LEADS = ["lead0", "lead1", "lead2", "lead3"]
# The interrupt fires before this lead index — the "eureka" mid-run pause.
PAUSE_BEFORE = 2


# --------------------------------------------------------------------------- #
# Fixtures — real Postgres, a deliberately NON-idempotent side-effect table.
# --------------------------------------------------------------------------- #

_SENDS_DDL = """
CREATE TABLE IF NOT EXISTS durable_run_test_sends (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id     text NOT NULL,
    lead_id    text NOT NULL,           -- NO unique key: a re-fire WOULD duplicate
    created_at timestamptz NOT NULL DEFAULT now()
);
"""


@pytest.fixture
def dsn() -> str:
    d = default_dsn()
    try:
        with psycopg.connect(d, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(
            f"local Postgres not reachable at {d} ({exc}). "
            "Start it with: cd infra && docker compose up -d",
            allow_module_level=True,
        )
    ensure_schema(d)
    with psycopg.connect(d, autocommit=True) as conn:
        conn.execute(_SENDS_DDL)
    return d


@pytest.fixture
def run_id(dsn: str) -> str:
    """A unique run id, with its rows cleaned up after the test."""
    rid = f"test-durable-{uuid.uuid4().hex[:12]}"
    yield rid
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM durable_run_test_sends WHERE run_id = %s", (rid,))
        conn.execute("DELETE FROM durable_step_ledger WHERE run_id = %s", (rid,))
        conn.execute("DELETE FROM durable_run_checkpoint WHERE run_id = %s", (rid,))


def _sends(dsn: str, run_id: str) -> list[str]:
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT lead_id FROM durable_run_test_sends WHERE run_id = %s ORDER BY id",
            (run_id,),
        ).fetchall()
    return [r["lead_id"] for r in rows]


def _row(dsn: str, run_id: str) -> dict | None:
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        return conn.execute(
            "SELECT * FROM durable_run_checkpoint WHERE run_id = %s", (run_id,)
        ).fetchone()


def _make_body(fired: list[str], *, reject_stops: bool = False):
    """A campaign-shaped run body: for each lead, stage a NON-idempotent 'send'
    through ``step()``; pause once before ``PAUSE_BEFORE`` to ask the operator.

    ``fired`` records every lead the side-effect ACTUALLY ran for in the current
    drive (empty after a restart) — so a test can assert what re-fired.
    If ``reject_stops`` and the operator answers 'reject', the remaining leads
    are not staged (HELD / approve-first flows through the interrupt).
    """

    def body(run: DurableRun):
        decision = None
        for i, lead in enumerate(LEADS):
            if i == PAUSE_BEFORE and decision is None:
                decision = run.interrupt(
                    {"ask": f"approve staging {LEADS[PAUSE_BEFORE:]}?", "run": run.run_id}
                )
                if reject_stops and decision == "reject":
                    run.set_state(rejected_at=i)
                    return {"decision": decision, "staged": i}

            def _send(conn, lead=lead):
                conn.execute(
                    "INSERT INTO durable_run_test_sends (run_id, lead_id) VALUES (%s, %s)",
                    (run.run_id, lead),
                )
                fired.append(lead)
                return {"lead": lead, "staged": True}

            run.step(f"{run.run_id}:{lead}:stage", _send)
            run.set_state(processed=(run.state.get("processed") or []) + [lead])
            run.checkpoint(cursor=i + 1)
        return {"decision": decision, "staged": len(LEADS)}

    return body


# --------------------------------------------------------------------------- #
# 1) Interrupt persists the full checkpoint to REAL Postgres.
# --------------------------------------------------------------------------- #


def test_interrupt_persists_full_state_to_postgres(dsn, run_id):
    fired: list[str] = []
    out = DurableRun(run_id, "ladies8391", dsn=dsn).run(_make_body(fired))

    assert out.interrupted
    assert out.interrupt["ask"].startswith("approve staging")
    # Only the pre-pause leads ran; the effect landed in real Postgres.
    assert fired == LEADS[:PAUSE_BEFORE]
    assert _sends(dsn, run_id) == LEADS[:PAUSE_BEFORE]

    row = _row(dsn, run_id)
    assert row is not None
    assert row["status"] == "interrupted"
    assert row["cursor"] == PAUSE_BEFORE
    assert row["state"]["processed"] == LEADS[:PAUSE_BEFORE]
    assert row["interrupt"]["index"] == 0
    assert row["interrupt"]["payload"]["ask"].startswith("approve staging")


# --------------------------------------------------------------------------- #
# 2) THE headline proof: interrupt -> restart (fresh object) -> resume, and the
#    completed sends do NOT re-fire. Exactly-once across the restart.
# --------------------------------------------------------------------------- #


def test_restart_resume_exactly_once_no_refire(dsn, run_id):
    fired_drive1: list[str] = []
    out1 = DurableRun(run_id, "ladies8391", dsn=dsn).run(_make_body(fired_drive1))
    assert out1.interrupted
    assert fired_drive1 == ["lead0", "lead1"]
    assert _sends(dsn, run_id) == ["lead0", "lead1"]

    # --- simulate a PROCESS RESTART: brand-new object, brand-new connection,
    #     no shared in-memory state — rehydrated only from Postgres. ---
    resumed = DurableRun.load(run_id, dsn=dsn)
    assert resumed.status == "interrupted"
    assert resumed.state["processed"] == ["lead0", "lead1"]  # durable state survived

    fired_drive2: list[str] = []
    out2 = resumed.resume(Command(resume="approved"), _make_body(fired_drive2))

    assert out2.completed
    assert out2.result == {"decision": "approved", "staged": 4}
    # ONLY the remaining leads fired on resume — 0,1 were ledger no-ops.
    assert fired_drive2 == ["lead2", "lead3"], "completed steps must NOT re-fire"

    # Exactly-once end to end: each lead staged exactly once in real Postgres.
    all_sends = _sends(dsn, run_id)
    assert all_sends == LEADS
    assert len(all_sends) == 4 and len(set(all_sends)) == 4

    row = _row(dsn, run_id)
    assert row["status"] == "completed"
    assert row["interrupt"] is None
    assert row["result"] == {"decision": "approved", "staged": 4}


# --------------------------------------------------------------------------- #
# 3) Crash-window rigor: a crash AFTER a step commits but BEFORE the checkpoint
#    advances must still not re-fire on recovery (the window my prior double-fire
#    taught me to enumerate).
# --------------------------------------------------------------------------- #


def test_crash_between_step_commit_and_checkpoint_no_refire(dsn, run_id):
    fired: list[str] = []

    class _Boom(RuntimeError):
        pass

    def crashy_body(run: DurableRun):
        # Stage lead0 (commits effect + ledger), then die BEFORE checkpoint().
        def _send(conn):
            conn.execute(
                "INSERT INTO durable_run_test_sends (run_id, lead_id) VALUES (%s, %s)",
                (run.run_id, "lead0"),
            )
            fired.append("lead0")
            return {"lead": "lead0"}

        run.step(f"{run.run_id}:lead0:stage", _send)
        raise _Boom("crash after step commit, before checkpoint")

    run = DurableRun(run_id, "ladies8391", dsn=dsn)
    with pytest.raises(_Boom):
        run.run(crashy_body)  # inserts the 'running' row, then dies mid-body

    # The effect committed once; the ledger holds the claim.
    assert _sends(dsn, run_id) == ["lead0"]
    assert DurableRun.load(run_id, dsn=dsn).has_run_step(f"{run_id}:lead0:stage")

    # Recovery: re-drive the (now non-crashing) body. lead0 is a ledger no-op.
    fired.clear()
    ok_fired: list[str] = []
    out = DurableRun.load(run_id, dsn=dsn).run(_make_body(ok_fired))
    # run() on a 'running' (crashed) row recovers; body then pauses at lead2.
    assert out.interrupted
    assert ok_fired == ["lead1"], "lead0 recovered as a no-op; only lead1 newly fired"
    assert _sends(dsn, run_id) == ["lead0", "lead1"], "lead0 not duplicated"


# --------------------------------------------------------------------------- #
# 4) step() is atomic: if the effect fn fails mid-way, NOTHING persists — no
#    partial send, no phantom ledger claim — and a retry runs cleanly once.
# --------------------------------------------------------------------------- #


def test_step_is_atomic_on_failure(dsn, run_id):
    run = DurableRun(run_id, "ladies8391", dsn=dsn)

    class _Fail(RuntimeError):
        pass

    def _bad(conn):
        conn.execute(
            "INSERT INTO durable_run_test_sends (run_id, lead_id) VALUES (%s, %s)",
            (run_id, "leadX"),
        )
        raise _Fail("effect crashed after insert, before commit")

    with pytest.raises(_Fail):
        run.step(f"{run_id}:leadX:stage", _bad)

    # Atomic rollback: neither the send nor the ledger claim survived.
    assert _sends(dsn, run_id) == []
    assert not run.has_run_step(f"{run_id}:leadX:stage")

    # A clean retry stages it exactly once.
    calls = {"n": 0}

    def _good(conn):
        calls["n"] += 1
        conn.execute(
            "INSERT INTO durable_run_test_sends (run_id, lead_id) VALUES (%s, %s)",
            (run_id, "leadX"),
        )
        return {"ok": True}

    assert run.step(f"{run_id}:leadX:stage", _good) == {"ok": True}
    assert run.step(f"{run_id}:leadX:stage", _good) == {"ok": True}  # 2nd = no-op
    assert calls["n"] == 1
    assert _sends(dsn, run_id) == ["leadX"]


# --------------------------------------------------------------------------- #
# 5) HELD / approve-first preserved: an operator 'reject' answer flows through
#    the interrupt and stops the remaining sends — nothing is staged past it.
# --------------------------------------------------------------------------- #


def test_reject_answer_holds_remaining_sends(dsn, run_id):
    fired1: list[str] = []
    DurableRun(run_id, "ladies8391", dsn=dsn).run(_make_body(fired1, reject_stops=True))

    fired2: list[str] = []
    out = DurableRun.load(run_id, dsn=dsn).resume(
        Command(resume="reject"), _make_body(fired2, reject_stops=True)
    )

    assert out.completed
    assert out.result == {"decision": "reject", "staged": PAUSE_BEFORE}
    # Nothing fired on resume; the remaining leads were HELD, not sent.
    assert fired2 == []
    assert _sends(dsn, run_id) == LEADS[:PAUSE_BEFORE]


# --------------------------------------------------------------------------- #
# 6) Replay guards (fk5 analogue) + resume hygiene.
# --------------------------------------------------------------------------- #


def test_run_of_completed_run_is_rejected(dsn, run_id):
    fired: list[str] = []
    r = DurableRun(run_id, "ladies8391", dsn=dsn)
    r.run(_make_body(fired))
    DurableRun.load(run_id, dsn=dsn).resume(Command(resume="approved"), _make_body([]))

    with pytest.raises(RunAlreadyCompletedError):
        DurableRun(run_id, "ladies8391", dsn=dsn).run(_make_body([]))


def test_run_on_paused_run_routes_to_resume(dsn, run_id):
    DurableRun(run_id, "ladies8391", dsn=dsn).run(_make_body([]))
    with pytest.raises(DurableResumeError):
        DurableRun(run_id, "ladies8391", dsn=dsn).run(_make_body([]))


def test_double_resume_is_rejected(dsn, run_id):
    DurableRun(run_id, "ladies8391", dsn=dsn).run(_make_body([]))
    resumed = DurableRun.load(run_id, dsn=dsn)
    resumed.resume(Command(resume="approved"), _make_body([]))
    with pytest.raises(DurableResumeError):
        resumed.resume(Command(resume="approved"), _make_body([]))


def test_load_missing_run_raises(dsn):
    with pytest.raises(RunNotFoundError):
        DurableRun.load("no-such-run-xyz", dsn=dsn)


# --------------------------------------------------------------------------- #
# 6b) ADVERSARIAL: concurrent resume of the SAME paused run. Two processes race
#     to load()+resume() the same run (the exact double-fire a skeptic reaches
#     for). The step ledger's atomic claim (INSERT ... ON CONFLICT DO NOTHING +
#     effect committed in one tx) is serialized by the unique index, so each
#     side-effect fires EXACTLY ONCE even though both drivers replay the body.
# --------------------------------------------------------------------------- #


def test_concurrent_resume_does_not_double_fire(dsn, run_id):
    # Pause after leads 0,1.
    DurableRun(run_id, "ladies8391", dsn=dsn).run(_make_body([]))
    assert _sends(dsn, run_id) == ["lead0", "lead1"]

    barrier = threading.Barrier(2)

    def racer(_):
        barrier.wait()  # release both threads at once to maximize the race
        try:
            return DurableRun.load(run_id, dsn=dsn).resume(
                Command(resume="approved"), _make_body([])
            ).status
        except DurableResumeError:
            # The loser may see the winner already flipped the run off 'interrupted'.
            return "already-resumed"

    with ThreadPoolExecutor(max_workers=2) as ex:
        outcomes = [f.result() for f in [ex.submit(racer, i) for i in range(2)]]

    # No lead re-fired despite two concurrent drivers replaying the body.
    all_sends = _sends(dsn, run_id)
    assert all_sends == LEADS, f"double-fire under concurrent resume: {all_sends}"
    assert len(set(all_sends)) == 4
    # At least one resume drove the run to completion.
    assert "completed" in outcomes
    assert _row(dsn, run_id)["status"] == "completed"


def test_concurrent_fresh_run_does_not_double_fire(dsn, run_id):
    """Two racers starting the SAME fresh run_id -> each lead staged exactly once."""
    barrier = threading.Barrier(2)

    def racer(_):
        barrier.wait()
        try:
            return DurableRun(run_id, "ladies8391", dsn=dsn).run(_make_body([])).status
        except (DurableResumeError, RunAlreadyCompletedError):
            return "guarded"

    with ThreadPoolExecutor(max_workers=2) as ex:
        [f.result() for f in [ex.submit(racer, i) for i in range(2)]]

    sends = _sends(dsn, run_id)
    assert sends == LEADS[:PAUSE_BEFORE], f"double-fire under concurrent run: {sends}"
    assert len(set(sends)) == PAUSE_BEFORE


# --------------------------------------------------------------------------- #
# 7) Multiple pause points in one long-horizon run: it pauses at each, resumes
#    through both, and every side-effect still fires exactly once.
# --------------------------------------------------------------------------- #


def test_two_interrupts_pause_twice_then_complete(dsn, run_id):
    fired: list[str] = []

    def body(run: DurableRun):
        answers = []
        for i, lead in enumerate(LEADS):
            if i in (1, 3):
                answers.append(run.interrupt({"ask": f"gate before {lead}"}))

            def _send(conn, lead=lead):
                conn.execute(
                    "INSERT INTO durable_run_test_sends (run_id, lead_id) VALUES (%s, %s)",
                    (run.run_id, lead),
                )
                fired.append(lead)
                return {"lead": lead}

            run.step(f"{run.run_id}:{lead}", _send)
            run.checkpoint(cursor=i + 1)
        return {"answers": answers}

    out1 = DurableRun(run_id, "ladies8391", dsn=dsn).run(body)
    assert out1.interrupted and out1.interrupt_index == 0

    out2 = DurableRun.load(run_id, dsn=dsn).resume(Command(resume="a1"), body)
    assert out2.interrupted and out2.interrupt_index == 1

    out3 = DurableRun.load(run_id, dsn=dsn).resume(Command(resume="a2"), body)
    assert out3.completed
    assert out3.result == {"answers": ["a1", "a2"]}
    # Exactly-once across BOTH pauses and restarts.
    assert _sends(dsn, run_id) == LEADS
    assert len(set(_sends(dsn, run_id))) == 4


# --------------------------------------------------------------------------- #
# 8) Unit-level: the step ledger dedups by (run_id, step_key); the interrupt
#    raises first, returns the answer on replay.
# --------------------------------------------------------------------------- #


def test_step_ledger_dedups_by_key(dsn, run_id):
    run = DurableRun(run_id, "ladies8391", dsn=dsn)
    calls = {"a": 0, "b": 0}

    def mk(name):
        def fn(conn):
            calls[name] += 1
            return {"name": name}

        return fn

    assert run.step(f"{run_id}:a", mk("a")) == {"name": "a"}
    assert run.step(f"{run_id}:a", mk("a")) == {"name": "a"}  # dedup -> no re-run
    assert run.step(f"{run_id}:b", mk("b")) == {"name": "b"}  # distinct key runs
    assert calls == {"a": 1, "b": 1}


def test_interrupt_raises_then_returns_answer_on_replay(dsn, run_id):
    run = DurableRun(run_id, "ladies8391", dsn=dsn)

    def body(r: DurableRun):
        return {"answer": r.interrupt({"ask": "go?"})}

    out = run.run(body)
    assert out.interrupted
    with pytest.raises(DurableInterrupt):
        # The raw pause: invoking the body with no answer queued raises.
        body(DurableRun.load(run_id, dsn=dsn))

    out2 = DurableRun.load(run_id, dsn=dsn).resume(Command(resume="yes"), body)
    assert out2.completed and out2.result == {"answer": "yes"}
