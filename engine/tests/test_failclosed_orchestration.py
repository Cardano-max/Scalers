"""Fail-closed campaign orchestration — regression tests for CustomerAcq-0dy + -37y.

THE BUG (reproduced on run ``team-camp_fa3aa98cd10a-3541ee7c28c2``): the strategist
cell AND every critic pass errored (credit-out — ``ModelHTTPError``/``CellExecutionError``),
yet the run was marked ``status='completed'`` and the drafts were staged as if the
pipeline had passed. A required quality gate failing must FAIL CLOSED:

  * 0dy — the run is NOT marked ``completed`` when a required step (strategist / critic /
    jury) failed; the failure is surfaced (agent, step, error, retryable, can_continue,
    impact); drafts are not finalized.
  * 37y — drafts are ``pending_review`` and NOT approved until critic AND jury actually
    pass; if the jury did not or could not pass (upstream failure), drafts are not approved.

Credit-INDEPENDENT: the failure is injected by stubbing the strategist/critic cell to
raise a credit ``ModelHTTPError`` — no real model call, no real credit state. The offline
DB+cell harness is reused from ``test_provided_leads_real_team`` so this runs with no
Postgres and no network.
"""

from __future__ import annotations

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from studio.agui import _execute_provided_leads_sync
from studio.campaign_runner import (
    FAILCLOSED_REQUIRED_ROLES,
    campaign_run_status,
    required_step_failures,
)

# Reuse the proven offline harness (fakes every DB + cell seam; injects cell excs).
from tests.test_provided_leads_real_team import _plan, _roles, _wire


def _credit_error() -> ModelHTTPError:
    """A real credit-out shape: HTTP 402 from the model provider."""
    return ModelHTTPError(
        status_code=402,
        model_name="anthropic:claude-opus-4-8",
        body={"error": {"type": "billing", "message": "credit balance is too low"}},
    )


# --------------------------------------------------------------------------- #
# Pure-unit: the shared fail-closed decision (used by BOTH the provided path and
# the compose path, and by the runs-row materializer that writes the DB status).
# --------------------------------------------------------------------------- #

def _ok(role: str) -> dict:
    return {"role": role, "output": {"ok": True}}


def _failed_strategist() -> dict:
    return {"role": "strategist", "output": {"status": "failed", "error": "ModelHTTPError: 402"}}


def _errored_critic() -> dict:
    return {"role": "critic", "output": {"verdict": "error", "rationale": "critic cell failed"}}


def test_required_step_failures_flags_failed_strategist():
    runs = [_ok("planner"), _failed_strategist(), _ok("draft")]
    fails = required_step_failures(runs)
    agents = {f["agent"] for f in fails}
    assert "strategist" in agents
    entry = next(f for f in fails if f["agent"] == "strategist")
    # Structured failure record the operator asked for.
    for key in ("agent", "step_id", "error", "retryable", "can_continue", "impact"):
        assert key in entry, key
    assert entry["can_continue"] is False
    assert "402" in entry["error"]


def test_required_step_failures_flags_all_error_critic():
    runs = [_ok("strategist"), _errored_critic(), _errored_critic()]
    fails = required_step_failures(runs)
    assert any(f["agent"] == "critic" for f in fails)


def test_one_good_critic_among_many_does_not_fail_the_run():
    # A single transient per-lead critic hiccup does NOT fail the whole run when another
    # critic pass succeeded — the gate is satisfied by >=1 real pass.
    runs = [_ok("strategist"), _errored_critic(), _ok("critic"), _ok("jury")]
    fails = required_step_failures(runs, ("strategist", "critic"))
    assert fails == []
    assert campaign_run_status(runs, ("strategist", "critic")) == "completed"


def test_campaign_run_status_failed_when_required_step_failed():
    runs = [_failed_strategist(), _errored_critic()]
    assert campaign_run_status(runs) == "failed"


def test_campaign_run_status_completed_when_all_required_pass():
    runs = [_ok("strategist"), _ok("critic"), _ok("jury")]
    assert campaign_run_status(runs) == "completed"


def test_failclosed_required_roles_cover_the_quality_gates():
    assert set(FAILCLOSED_REQUIRED_ROLES) >= {"strategist", "critic"}


# --------------------------------------------------------------------------- #
# Provided-leads path (the reproduced bug): a stubbed credit failure must fail closed.
# --------------------------------------------------------------------------- #

def test_strategist_credit_out_does_not_complete_the_run(monkeypatch):
    _wire(monkeypatch, strat_exc=_credit_error())
    summary = _execute_provided_leads_sync(_plan(), "sess1", "ladies8391", None, None)

    # 0dy: the run is NOT completed when the required strategist step failed.
    assert summary["run_status"] != "completed"
    assert summary["run_status"] == "failed"
    # The failure is surfaced with the required fields.
    fs = summary["failure_summary"]
    assert fs, "failure_summary must be populated when a required step failed"
    assert any(f["agent"] == "strategist" for f in fs)
    assert all({"agent", "error", "retryable", "can_continue", "impact"} <= set(f) for f in fs)


def test_critic_credit_out_fails_closed_and_blocks_the_jury(monkeypatch):
    _wire(monkeypatch, crit_exc=_credit_error())
    summary = _execute_provided_leads_sync(_plan(), "sess1", "ladies8391", None, None)

    # 0dy: every critic errored -> run not completed.
    assert summary["run_status"] == "failed"
    assert any(f["agent"] == "critic" for f in summary["failure_summary"])

    # 37y: the jury cannot pass on top of an all-errored critic -> drafts NOT approved.
    jury = next(ar for ar in summary["agent_runs"] if ar["role"] == "jury")
    assert jury["output"]["decision"] == "blocked"
    assert jury["output"]["aggregate"] == 0.0


def test_healthy_run_still_completes_and_jury_reviews(monkeypatch):
    # Guard against an over-eager fail-closed: a clean run STILL completes and the jury
    # sends drafts to review (drafts pending_review, aggregate 1.0).
    _wire(monkeypatch)
    summary = _execute_provided_leads_sync(_plan(), "sess1", "ladies8391", None, None)

    assert summary["run_status"] == "completed"
    assert summary["failure_summary"] == []
    jury = next(ar for ar in summary["agent_runs"] if ar["role"] == "jury")
    assert jury["output"]["decision"] == "review"
    assert jury["output"]["aggregate"] == 1.0
    # The run still continued and staged its drafts (fail-closed only bites on failure).
    assert _roles(summary).count("draft") == 2


# --------------------------------------------------------------------------- #
# Real-Postgres proof: the reproduced symptom was a ``runs.status='completed'`` row
# in Postgres despite failed steps. This pins that a failed run now writes
# ``status='failed'`` to the real DB (mirrors the reproduced bug run
# ``team-camp_fa3aa98cd10a-...``).
# --------------------------------------------------------------------------- #

@pytest.mark.integration
def test_failed_run_writes_failed_status_to_postgres():
    import os
    import uuid

    import psycopg

    from studio.campaign_runner import _materialize_runs_row, campaign_run_status

    dsn = os.environ.get("ENGINE_DATABASE_URL") or "postgresql://scalers:scalers@localhost:5432/scalers"
    run_id = f"team-camp_fctest{uuid.uuid4().hex[:6]}-{uuid.uuid4().hex[:8]}"
    # The reproduced signature: strategist status='failed', every critic verdict='error'.
    agent_runs = [
        {"role": "planner", "model": "grounded_rules", "input": {}, "output": {"ok": True}},
        {"role": "strategist", "model": "m", "input": {},
         "output": {"status": "failed", "error": "ModelHTTPError: 402 credit balance too low"}},
        {"role": "draft", "model": "m", "input": {}, "output": {"hook": ""}},
        {"role": "critic", "model": "m", "input": {},
         "output": {"verdict": "error", "rationale": "critic cell failed: CellExecutionError"}},
        {"role": "jury", "model": "m", "input": {},
         "output": {"aggregate": 0.0, "decision": "blocked", "status": "failed"}},
    ]
    assert campaign_run_status(agent_runs) == "failed"

    try:
        ok = _materialize_runs_row(
            dsn=dsn, run_id=run_id, tenant_id="ladies8391", agent_runs=agent_runs,
            terminal_status=campaign_run_status(agent_runs),
        )
        if not ok:
            pytest.skip("runs store unavailable (no live Postgres)")
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            row = conn.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()
        assert row is not None, "runs row was not written"
        assert row[0] == "failed", f"expected DB status 'failed', got {row[0]!r}"
    finally:
        try:
            with psycopg.connect(dsn, connect_timeout=5) as conn:
                conn.execute("DELETE FROM runs WHERE run_id=%s", (run_id,))
                conn.commit()
        except Exception:
            pass
