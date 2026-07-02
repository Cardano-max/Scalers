"""fr1.2 AC4 — CRASH-MID-CAMPAIGN recovery on the provided-leads run (real Postgres).

Drives the REAL ``_execute_provided_leads_sync`` with fake cells (no LLM/network) but
the REAL ``actions`` store + REAL ``studio.durable_run`` step ledger on local Postgres,
kills the run at the enumerated crash windows (docs/design/
fr1.2-durable-activation-crash-windows.md), restarts with the SAME ``run_id``, and
asserts the super-required AC:

  * no lead skipped        — every lead ends with exactly one staged action row;
  * no lead double-drafted — a lead fully staged+ledgered before the crash is never
    re-drafted on restart (ledger replay-skip);
  * no side effect re-fired — the ``actions`` idempotency key admits exactly one row
    per (run_id, cust_id) even when the ledger lagged the row (window W2).

Windows covered: W1/W6 via the crash-on-lead-2 restart (lead 2 itself re-processes
from scratch), W3 via the between-leads crash (lead 1 skip), W2 via the pre-seeded
row-without-ledger state (what a kill between the staged row's commit and the ledger
commit durably leaves), and whole-run replay (fk5 analogue) via the completed re-run.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

import cells.critic as critic_mod
import cells.strategy as strategy_mod
import memory as memory_mod
import studio.adapters.message_source as msg_mod
import studio.agui as agui
import studio.campaign_runner as runner_mod
import studio.customer_research as cr
import studio.offers as offers_mod
import studio.psych_profile as psych
import team.store as team_store_mod
from actions.store import list_actions_for_run
from studio.agui import CampaignPlan, _execute_provided_leads_sync

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("ENGINE_DATABASE_URL"),
        reason="requires Postgres (set ENGINE_DATABASE_URL)",
    ),
]

DSN = os.environ.get("ENGINE_DATABASE_URL", "postgresql://scalers:scalers@localhost:5432/scalers")
LEAD_IDS = ["c1", "c2", "c3"]


# ── fakes (mirrors test_provided_leads_real_team._wire; actions + ledger REAL) ──


class _FakeCell:
    def __init__(self, out):
        self._out = out
        self.model = "anthropic:claude-sonnet-4-5"

    def run_sync(self, prompt):
        return self._out


class _Strategy:
    target_angle = "warm personal win-back"

    def model_dump(self):
        return {"target_angle": self.target_angle, "positioning": "p",
                "key_messages": ["m"], "channel_rationale": "email"}


class _Verdict:
    value = "approve"


class _Critique:
    verdict = _Verdict()
    confidence = 0.9
    rationale = "grounded"


class _FakeTeamStore:
    def __init__(self, *a, **k):
        self.runs: list[dict] = []

    def setup(self):
        pass

    def record_agent_run(self, **kw):
        self.runs.append(kw)


class _FakeMemory:
    def __init__(self, *a, **k):
        pass

    def ensure_schema(self):
        pass

    def write(self, **kw):
        pass


class _FakeConvSource:
    def __init__(self, *a, **k):
        pass

    def thread_for(self, cust_id):
        return None


class _Boom(RuntimeError):
    """The injected 'process killed here' stand-in."""


def _wire(monkeypatch, drafted: list[str], *, crash_on: str | None = None):
    """Fake every cell/store seam EXCEPT actions.store + studio.durable_run (the
    exactly-once machinery under test, on real PG). ``drafted`` records each
    cust_id whose draft cell ran — the no-double-draft evidence. ``crash_on``
    kills the run (raises) at that lead's research step: AFTER the prior lead
    fully staged + ledgered, BEFORE this lead stages anything — the between-leads
    kill window (W3 for the prior lead, W1 for this one)."""
    monkeypatch.setattr(memory_mod, "MemoryStore", _FakeMemory)
    monkeypatch.setattr(team_store_mod, "TeamStore", _FakeTeamStore)
    monkeypatch.setattr(runner_mod, "_materialize_runs_row", lambda **kw: False)
    monkeypatch.setattr(agui, "_log_turn", lambda *a, **k: None)
    monkeypatch.setattr(agui, "_persist_plan", lambda *a, **k: None)
    monkeypatch.setattr(msg_mod, "DbConversationSource", _FakeConvSource)
    monkeypatch.setattr(offers_mod, "get_offers", lambda *a, **k: [])
    monkeypatch.setattr(psych, "analyze_customer", lambda facts, thread=None, **k: None)

    monkeypatch.setattr(cr, "_research_enabled", lambda v: False)

    def _research(facts, *, enabled):
        if crash_on is not None and facts["customer_id"] == crash_on:
            raise _Boom(f"killed at {crash_on}")  # unwrapped call -> the drive dies
        return []

    monkeypatch.setattr(cr, "research_studio", _research)
    monkeypatch.setattr(
        cr, "lookup_leads",
        lambda tenant, rows, *, dsn=None, memory_store=None: [
            {"customer_id": r["customer_id"], "name": f"Lead {r['customer_id']}",
             "email": f"{r['customer_id']}@lead.example",
             "tattoo_history": [], "persona_traits": {}, "interests": [], "memories": []}
            for r in rows
        ],
    )

    def _fake_draft(facts, *, goal="", **kw):
        drafted.append(facts["customer_id"])
        return {
            "channel": "gmail", "target": f"{facts['customer_id']}@lead.example",
            "subject": "We miss you", "draft": "Come back for a fresh piece.",
            "grounding": [f"name={facts['name']}"], "customer_id": facts["customer_id"],
            "copy_model": "anthropic:claude-haiku-4-5",
        }

    monkeypatch.setattr(cr, "build_outreach_draft", _fake_draft)
    monkeypatch.setattr(strategy_mod, "build_strategy_prompt", lambda *a, **k: "sp")
    monkeypatch.setattr(strategy_mod, "build_strategy_cell", lambda **k: _FakeCell(_Strategy()))
    monkeypatch.setattr(critic_mod, "build_critic_cell", lambda **k: _FakeCell(_Critique()))


def _activate_durable(monkeypatch):
    """Flip the fr1.2 activation seam for this process: ENGINE_DATABASE_URL -> the
    cached Settings must re-read it (get_settings is lru_cached)."""
    from harness.config import get_settings

    monkeypatch.setenv("ENGINE_DATABASE_URL", DSN)
    get_settings.cache_clear()  # the run_ctx fixture re-clears on teardown


def _plan() -> CampaignPlan:
    return CampaignPlan(
        lead_source="provided", goal="win back lapsed clients", channels=["gmail"],
        output_count=3,
        customers={"customer_ids": list(LEAD_IDS), "rows": 3, "columns": ["name", "email"]},
    )


@pytest.fixture()
def run_ctx(monkeypatch):
    """Fresh run/tenant ids + durable activation + teardown of this run's rows."""
    run_id = f"team-camp_{uuid.uuid4().hex[:8]}-{uuid.uuid4().hex[:8]}"
    tenant = f"fr12t_{uuid.uuid4().hex[:6]}"
    _activate_durable(monkeypatch)
    yield run_id, tenant
    from harness.config import get_settings

    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM actions WHERE run_id = %s", (run_id,))
        conn.execute("DELETE FROM durable_step_ledger WHERE run_id = %s", (run_id,))
        conn.execute("DELETE FROM durable_run_checkpoint WHERE run_id = %s", (run_id,))
    get_settings.cache_clear()  # drop the DSN this test forced into the cache


def _ledger_keys(run_id: str) -> set[str]:
    with psycopg.connect(DSN, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT step_key FROM durable_step_ledger WHERE run_id = %s", (run_id,)
        ).fetchall()
    return {r[0] for r in rows}


def _assert_exactly_once(run_id: str):
    """The AC's invariant: one staged row per lead, no dupes, full coverage."""
    rows = list_actions_for_run(run_id, dsn=DSN)
    assert len(rows) == len(LEAD_IDS)  # no lead skipped, no lead doubled
    keys = sorted(r.idempotency_key for r in rows)
    assert keys == sorted(f"{run_id}:{c}" for c in LEAD_IDS)
    with psycopg.connect(DSN, autocommit=True) as conn:
        dupes = conn.execute(
            "SELECT idempotency_key, count(*) FROM actions WHERE run_id = %s "
            "GROUP BY 1 HAVING count(*) > 1", (run_id,),
        ).fetchall()
    assert dupes == []  # no side effect re-fired


def test_kill_between_leads_resumes_at_exact_lead(monkeypatch, run_ctx):
    """AC4 window W3/W1: lead 1 fully staged + ledgered, process killed before lead 2
    stages anything. Restart with the SAME run_id: lead 1 is replay-skipped (never
    re-drafted), lead 2 is the exact lead the resume starts at, all 3 end staged."""
    run_id, tenant = run_ctx
    drafted: list[str] = []

    _wire(monkeypatch, drafted, crash_on="c2")
    with pytest.raises(_Boom):
        _execute_provided_leads_sync(_plan(), "sess", tenant, DSN, run_id)

    # Durable state the kill left: c1 staged + ledgered; c2/c3 nothing.
    assert drafted == ["c1"]
    assert _ledger_keys(run_id) == {f"{run_id}:c1:stage"}
    assert [r.idempotency_key for r in list_actions_for_run(run_id, dsn=DSN)] == [f"{run_id}:c1"]

    # RESTART (fresh drive, same run_id), crash cleared.
    _wire(monkeypatch, drafted, crash_on=None)
    _execute_provided_leads_sync(_plan(), "sess", tenant, DSN, run_id)

    # Resumed at the exact lead being drafted: c1 NOT re-drafted; c2 then c3 drafted.
    assert drafted == ["c1", "c2", "c3"]
    assert _ledger_keys(run_id) == {f"{run_id}:{c}:stage" for c in LEAD_IDS}
    _assert_exactly_once(run_id)


def test_kill_mid_lead_between_staged_row_and_ledger_never_refires(monkeypatch, run_ctx):
    """AC4 window W2 (mid-lead): the kill lands AFTER the lead's action row committed
    but BEFORE its ledger record — exactly the durable state we seed here (row for c2,
    no ledger). Restart: c2 is re-drafted (ledger honest: orchestration didn't finish
    — wasted work, accepted residual) but the idempotency key returns the EXISTING
    row id: still exactly one row per lead, nothing re-fires."""
    run_id, tenant = run_ctx
    from actions.store import ensure_schema, record_pending_action

    ensure_schema(DSN)
    pre_existing = record_pending_action(
        tenant_id=tenant, decision_id=None, type="outreach", channel="gmail",
        worker="studio_provided_leads", target="c2@lead.example",
        draft="Come back for a fresh piece.", subject="We miss you", context=None,
        conf=0.9, threshold=None, esc_kind="approval_required",
        esc_label="Provided-lead outreach — operator approval required",
        idempotency_key=f"{run_id}:c2", run_id=run_id, dsn=DSN,
    )
    assert not _ledger_keys(run_id)  # the W2 state: row committed, ledger empty

    drafted: list[str] = []
    _wire(monkeypatch, drafted, crash_on=None)
    _execute_provided_leads_sync(_plan(), "sess", tenant, DSN, run_id)

    # c2 WAS re-drafted (ledger had no claim) — the enumerated W2 residual...
    assert drafted == ["c1", "c2", "c3"]
    _assert_exactly_once(run_id)  # ...but never a second row for c2
    rows = {r.idempotency_key: r for r in list_actions_for_run(run_id, dsn=DSN)}
    assert rows[f"{run_id}:c2"].id == pre_existing  # the ORIGINAL row survived


def test_completed_run_replay_is_full_noop(monkeypatch, run_ctx):
    """Whole-run replay (the fk5 analogue at the loop level): re-driving a fully
    completed run_id re-drafts NOTHING and adds no rows."""
    run_id, tenant = run_ctx
    drafted: list[str] = []
    _wire(monkeypatch, drafted, crash_on=None)
    _execute_provided_leads_sync(_plan(), "sess", tenant, DSN, run_id)
    assert drafted == ["c1", "c2", "c3"]
    _assert_exactly_once(run_id)

    _execute_provided_leads_sync(_plan(), "sess", tenant, DSN, run_id)
    assert drafted == ["c1", "c2", "c3"]  # zero new drafts on replay
    _assert_exactly_once(run_id)          # zero new rows
